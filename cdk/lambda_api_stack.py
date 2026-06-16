"""Lambda + CloudFront deployment of the PyNightSky API (the App Runner successor).

The API runs as a container Lambda (the ECR ``:lambda`` image = our app + the Lambda
Web Adapter). Its Function URL uses AWS_IAM auth because the account SCP blocks public
(unauthenticated) Function URLs; CloudFront fronts it with Origin Access Control, which
SigV4-signs each request so the IAM-auth URL is reachable. CloudFront also caches GET
responses (keyed on the full query string) so repeat /night queries are edge-served and
skip Lambda cold starts.

Foundational resources (S3 raster bucket, DynamoDB cache table) are referenced, never
managed here. Names come from the environment so the public repo carries no identifiers.
"""
import os
import pathlib
import shutil

from aws_cdk import (
    Stack,
    BundlingOptions,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Tags,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_logs as logs,
    aws_sns as sns,
    aws_sqs as sqs,
    aws_certificatemanager as acm,
    aws_location as location,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_wafv2 as wafv2,
)
from constructs import Construct

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _stage_worker_src() -> str:
    """Stage the minimal source the worker zip needs as the bundling input.

    The runtime is GDAL/scipy/pyarrow-free (light-pollution rasters are read from S3
    as raw-binary grids with numpy+boto3; the PAD-US index is a numpy .npz), so the
    worker fits a zip Lambda (~169 MB unzipped < 250 MB). We ship the engine source,
    apps (minus the web SPA), the de421.bsp ephemeris (inside PyNightSkyPredictor/),
    the PAD-US .npz, and requirements.txt; bundling pip-installs the deps on top.
    """
    stage = _REPO / "cdk" / ".worker_build"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ig = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules", "web", "dist")
    shutil.copytree(_REPO / "PyNightSkyPredictor", stage / "PyNightSkyPredictor", ignore=ig)
    shutil.copytree(_REPO / "apps", stage / "apps", ignore=ig)
    (stage / "cache").mkdir()
    shutil.copy(_REPO / "cache" / "darkhours_padus_h3.npz", stage / "cache" / "darkhours_padus_h3.npz")
    shutil.copy(_REPO / "requirements.txt", stage / "requirements.txt")
    return str(stage)


class LambdaApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        raster_bucket = os.environ["PYNIGHTSKY_RASTER_BUCKET"]
        cache_table = os.environ["PYNIGHTSKY_CACHE_TABLE"]

        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Env", "prod")
        Tags.of(self).add("Component", "api")

        # --- M7.3: explicit log groups (controlled retention; metric filters reference them) ---
        api_log_group = logs.LogGroup(
            self, "ApiLogGroup",
            log_group_name="/pynightsky/api",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        worker_log_group = logs.LogGroup(
            self, "WorkerLogGroup",
            log_group_name="/pynightsky/worker",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- the API as a container Lambda (from the ECR image) ---
        # CI passes the immutable git SHA via `-c imageTag=<sha>` so each deploy
        # references a NEW tag — otherwise the mutable ":lambda" tag leaves the CFN
        # template unchanged and CloudFormation won't pick up a rebuilt image.
        image_tag = self.node.try_get_context("imageTag") or "lambda"
        repo = ecr.Repository.from_repository_name(self, "Repo", "pynightsky-api")
        fn = lambda_.DockerImageFunction(
            self, "Api",
            code=lambda_.DockerImageCode.from_ecr(repo, tag_or_digest=image_tag),
            memory_size=3008,
            timeout=Duration.seconds(120),
            tracing=lambda_.Tracing.ACTIVE,
            log_group=api_log_group,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
                "LOG_LEVEL": "INFO",
                # LWA routes non-HTTP Lambda events (EventBridge warmup pings) here
                "AWS_LWA_PASS_THROUGH_PATH": "/warmup",
            },
        )

        # --- Scheduled warmup ping — keeps one container alive, prevents cold starts ---
        # EventBridge invokes Lambda directly every 4 minutes; LWA converts to POST /warmup.
        # Cost: ~10,800 invocations/month + ~1,620 GB-s — both within Lambda free tier.
        warmup_rule = events.Rule(
            self, "WarmupRule",
            schedule=events.Schedule.rate(Duration.minutes(4)),
        )
        warmup_rule.add_target(targets.LambdaFunction(fn))

        # least-privilege data access (reference foundational resources)
        bucket = s3.Bucket.from_bucket_name(self, "RasterBucket", raster_bucket)
        table = dynamodb.Table.from_table_name(self, "CacheTable", cache_table)
        bucket.grant_read(fn)               # s3:GetObject on the raster bucket
        table.grant_read_write_data(fn)     # DynamoDB cache get/set/invalidate

        # --- AWS Location Service: Esri place index for forward + reverse geocoding ---
        # Replaces the public Nominatim API in Lambda (no rate-limit shared state across
        # invocations). Esri data provider: 20k req/mo free for 12 months, then $0.50/1k.
        # Aggressive DynamoDB caching (permanent for forward, 90-day for reverse) means
        # steady-state call volume is very low.
        place_index = location.CfnPlaceIndex(
            self, "PlaceIndex",
            index_name="pynightsky-place-index",
            data_source="Esri",
            pricing_plan="RequestBasedUsage",
        )
        place_index_arn = (
            f"arn:aws:geo:{self.region}:{self.account}"
            f":place-index/{place_index.index_name}"
        )
        geo_policy = iam.PolicyStatement(
            actions=["geo:SearchPlaceIndexForText", "geo:SearchPlaceIndexForPosition"],
            resources=[place_index_arn],
        )
        fn.add_to_role_policy(geo_policy)
        fn.add_environment("PYNIGHTSKY_PLACE_INDEX", place_index.index_name)

        route_calc = location.CfnRouteCalculator(
            self, "RouteCalculator",
            calculator_name="pynightsky-route-calculator",
            data_source="Esri",
            pricing_plan="RequestBasedUsage",
        )
        route_calc_arn = (
            f"arn:aws:geo:{self.region}:{self.account}"
            f":route-calculator/{route_calc.calculator_name}"
        )
        route_policy = iam.PolicyStatement(
            actions=["geo:CalculateRouteMatrix"],
            resources=[route_calc_arn],
        )
        fn.add_to_role_policy(route_policy)
        fn.add_environment("PYNIGHTSKY_ROUTE_CALCULATOR", route_calc.calculator_name)

        # --- async jobs: SQS queue + zip-Lambda worker (M6.3) ---
        # Long /calendar+/trip computes run off the request path. The API enqueues a
        # job; this worker runs plan_trip and writes the result into the cache, where
        # /jobs/{id} reads it. visibility_timeout must exceed the worker timeout; a DLQ
        # catches messages that can't be processed.
        #
        # The worker is a zip Lambda (not a container): with rasterio/GDAL, pyarrow and
        # scipy removed from the runtime, the deps fit the 250 MB zip limit (~169 MB
        # unzipped), which avoids the container image's first-invoke image-load latency.
        # Deps are pip-installed for the Lambda runtime (linux/amd64) by CDK bundling.
        dlq = sqs.Queue(self, "JobsDlq", retention_period=Duration.days(14))
        jobs_queue = sqs.Queue(
            self, "JobsQueue",
            visibility_timeout=Duration.seconds(960),     # > worker timeout (900s)
            retention_period=Duration.days(1),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )

        worker = lambda_.Function(
            self, "Worker",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.X86_64,
            handler="apps.worker.handler.handler",
            code=lambda_.Code.from_asset(
                _stage_worker_src(),
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    platform="linux/amd64",
                    command=[
                        "bash", "-c",
                        "pip install --no-cache-dir -r requirements.txt "
                        "python-json-logger aws-xray-sdk -t /asset-output "
                        "&& cp -r PyNightSkyPredictor apps cache /asset-output/",
                    ],
                ),
            ),
            memory_size=2048,
            timeout=Duration.seconds(900),                # 15 min: large multi-night trips
            tracing=lambda_.Tracing.ACTIVE,
            log_group=worker_log_group,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
                "LOG_LEVEL": "INFO",
            },
        )
        bucket.grant_read(worker)
        table.grant_read_write_data(worker)
        worker.add_to_role_policy(geo_policy)
        worker.add_environment("PYNIGHTSKY_PLACE_INDEX", place_index.index_name)
        worker.add_to_role_policy(route_policy)
        worker.add_environment("PYNIGHTSKY_ROUTE_CALCULATOR", route_calc.calculator_name)
        worker.add_event_source(lambda_events.SqsEventSource(jobs_queue, batch_size=1))

        # --- Scheduled worker warmup ping — keeps one worker container alive + primed ---
        # The worker is only invoked by SQS, so at sparse traffic nearly every job pays the
        # ~4.6s cold Init. EventBridge invokes the worker directly every 4 min with a
        # non-SQS event (no Records); the handler treats that as a warmup, runs the prewarm
        # synchronously, and returns. Cost: ~10,800 tiny invocations/month (free tier).
        worker_warmup_rule = events.Rule(
            self, "WorkerWarmupRule",
            schedule=events.Schedule.rate(Duration.minutes(4)),
        )
        worker_warmup_rule.add_target(
            targets.LambdaFunction(
                worker,
                event=events.RuleTargetInput.from_object({"warmup": True}),
            )
        )

        # The API can enqueue jobs and knows the queue URL (its presence flips the
        # endpoints from inline to async).
        jobs_queue.grant_send_messages(fn)
        fn.add_environment("PYNIGHTSKY_JOBS_QUEUE_URL", jobs_queue.queue_url)

        # Function URL — AWS_IAM auth (public NONE is SCP-blocked); CloudFront signs it.
        furl = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)

        # --- CloudFront in front, with OAC SigV4-signing the IAM-auth Function URL ---
        origin = origins.FunctionUrlOrigin.with_origin_access_control(furl)

        api_cache = cloudfront.CachePolicy(
            self, "ApiCachePolicy",
            comment="Cache API GETs by full query string",
            min_ttl=Duration.seconds(0),
            default_ttl=Duration.minutes(5),
            max_ttl=Duration.hours(1),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
            header_behavior=cloudfront.CacheHeaderBehavior.none(),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
            enable_accept_encoding_gzip=True,
        )

        # The async endpoints must NOT be cached: /trip + /calendar return a fresh
        # job_id each call, and /jobs/{id} status changes as the job runs. Disable
        # caching but still forward the query string to the origin.
        fwd_qs = cloudfront.OriginRequestPolicy(
            self, "FwdQueryString",
            comment="Forward query string to the Lambda origin (no caching)",
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
            header_behavior=cloudfront.OriginRequestHeaderBehavior.none(),
            cookie_behavior=cloudfront.OriginRequestCookieBehavior.none(),
        )

        # Viewer-request function: reject anything that isn't a same-origin browser fetch.
        # Browsers set Sec-Fetch-Site=same-origin automatically for SPA→API calls (same
        # CloudFront domain). The header is browser-controlled — JS cannot override it —
        # so requests from other websites arrive with "cross-site" and are blocked. Curl
        # and scripts send no header at all and are also blocked. The 403 is returned
        # before the request reaches the cache or Lambda. /healthz is excluded (open_cached
        # below) so uptime monitors can probe it freely.
        _sec_fetch_js = (
            "function handler(event){"
            "var h=event.request.headers['sec-fetch-site'];"
            "if(!h||h.value!=='same-origin')"
            "return{statusCode:403,statusDescription:'Forbidden'};"
            "return event.request;}"
        )
        sec_fetch_fn = cloudfront.Function(
            self, "SecFetchCheck",
            code=cloudfront.FunctionCode.from_inline(_sec_fetch_js),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )
        _sec_fetch = [cloudfront.FunctionAssociation(
            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
            function=sec_fetch_fn,
        )]

        no_cache = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=fwd_qs,
            function_associations=_sec_fetch,
        )
        # /night: cached GETs keyed on the full query string, same-origin guard applied.
        api_cached = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=api_cache,
            function_associations=_sec_fetch,
        )
        # /healthz: same cache policy, no guard so uptime monitors can reach it.
        open_cached = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=api_cache,
        )

        # --- M7.1: the SPA's static assets behind the SAME distribution ---
        # A private bucket (OAC, no public access) holds the built React app. Serving it
        # as the default behavior of this distribution means the SPA and API share one
        # origin — the SPA calls the API with relative paths (/night?...), so it is
        # same-origin: no CORS, no API URL baked into the bundle. apps/web/dist must be
        # built (npm run build) before synth; CI does this in deploy.yml.
        spa_bucket = s3.Bucket(
            self, "SpaBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,    # assets are rebuilt from source
            auto_delete_objects=True,
        )
        spa_origin = origins.S3BucketOrigin.with_origin_access_control(spa_bucket)

        # --- M7.2: WAF in front of the distribution ---
        # The CloudFront URL is public + unauthenticated, so before advertising it we put
        # an AWS WAFv2 WebACL on the distribution. A CLOUDFRONT-scoped WebACL MUST live in
        # us-east-1 (this stack is) and is attached via the distribution's web_acl_id (it
        # wants the ARN, not the id). Default action ALLOW — the rules below subtract.
        # Rules run in priority order (lowest first); first terminating match wins.
        #   0  AmazonIpReputationList  — drop traffic from AWS-tracked malicious IPs first
        #                                (cheapest reject; ~25 WCU).
        #   1  KnownBadInputs          — block request patterns tied to known exploits/probes
        #                                (~200 WCU). Low false-positive risk on a JSON API.
        #   2  RateLimitPerIp          — block any single IP exceeding 200 requests / 5 min.
        #                                Most legit /night hits are CloudFront cache hits and
        #                                never reach this count; this throttles scripted abuse.
        # Total ~227 WCU, well under the 1500-WCU default WebACL capacity. Managed groups use
        # override_action=none so each group's own block/count actions apply unchanged.
        managed = lambda name, vendor="AWS": wafv2.CfnWebACL.StatementProperty(
            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                vendor_name=vendor, name=name,
            ),
        )
        vis = lambda metric: wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True,
            metric_name=metric,
            sampled_requests_enabled=True,
        )
        web_acl = wafv2.CfnWebACL(
            self, "ApiWaf",
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=vis("PyNightSkyWaf"),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AmazonIpReputation",
                    priority=0,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=managed("AWSManagedRulesAmazonIpReputationList"),
                    visibility_config=vis("AmazonIpReputation"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="KnownBadInputs",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=managed("AWSManagedRulesKnownBadInputsRuleSet"),
                    visibility_config=vis("KnownBadInputs"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitPerIp",
                    priority=2,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=200,                 # requests per IP per 5-min window
                            aggregate_key_type="IP",
                        ),
                    ),
                    visibility_config=vis("RateLimitPerIp"),
                ),
            ],
        )

        # --- Custom domain: darkhours.app ---
        # from_lookup queries Route 53 at synth time and caches in cdk.context.json.
        # The ACM certificate uses DNS validation: CDK automatically creates the CNAME
        # validation record in the hosted zone so no manual console steps are needed.
        # CloudFront requires the cert to be in us-east-1 — this stack already is.
        hosted_zone = route53.HostedZone.from_lookup(
            self, "Zone",
            domain_name="darkhours.app",
        )
        cert = acm.Certificate(
            self, "Cert",
            domain_name="darkhours.app",
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        dist = cloudfront.Distribution(
            self, "Cdn",
            comment="PyNightSky SPA + API (S3 default, Lambda for API paths)",
            default_root_object="index.html",
            web_acl_id=web_acl.attr_arn,
            domain_names=["darkhours.app"],
            certificate=cert,
            default_behavior=cloudfront.BehaviorOptions(
                origin=spa_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            additional_behaviors={
                "/night":    api_cached,
                "/healthz":  open_cached,
                "/calendar": no_cache,
                "/trip":     no_cache,
                "/nearby":   no_cache,
                "/jobs/*":   no_cache,
            },
        )

        # Upload the built SPA and invalidate the edge cache on every deploy. The asset
        # path is resolved relative to this file so it works regardless of cwd.
        spa_dist_path = os.path.join(os.path.dirname(__file__), "..", "apps", "web", "dist")
        s3deploy.BucketDeployment(
            self, "SpaDeploy",
            sources=[s3deploy.Source.asset(spa_dist_path)],
            destination_bucket=spa_bucket,
            distribution=dist,
            distribution_paths=["/*"],
        )

        # CloudFront→Function-URL needs BOTH invoke actions. The OAC helper above only
        # grants lambda:InvokeFunctionUrl; since an AWS change (Oct 2025) Function URL
        # invocation *also* requires lambda:InvokeFunction. Add it explicitly, scoped by
        # SourceArn to this one distribution. (Without it CloudFront gets 403.)
        # M7.1 revisit (SPA now live on this same distribution): the grant is REQUIRED for
        # API ingress and is already minimal — single action, single principal, SourceArn-
        # locked to this distribution. Kept as-is; do not remove.
        fn.add_permission(
            "CdnInvokeFunction",
            principal=iam.ServicePrincipal("cloudfront.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=(
                f"arn:aws:cloudfront::{self.account}:distribution/{dist.distribution_id}"
            ),
        )

        # --- M7.3: upstream-error alarm + WAF request logging ---
        # Metric filter on ERROR-level JSON records in the API log group. Any upstream
        # call that fails hard (AWS Location/Celestrak log at ERROR; 7Timer logs at WARNING
        # but also shows up in Logs Insights via the `service` field). A single alarm
        # keeps it simple; drill down with Logs Insights `| filter service = "aws-location"`.
        api_log_group.add_metric_filter(
            "UpstreamErrorMetric",
            metric_name="UpstreamErrors",
            metric_namespace="PyNightSky",
            metric_value="1",
            filter_pattern=logs.FilterPattern.string_value("$.levelname", "=", "ERROR"),
        )

        # SNS topic for alarm notifications. Subscribe your email after deploy:
        #   aws sns subscribe --profile <profile> --topic-arn <AlarmTopicArn> \
        #     --protocol email --notification-endpoint <your@email.com>
        alarm_topic = sns.Topic(self, "AlarmTopic", display_name="PyNightSky Alarms")

        # Alarm: >= 3 upstream ERRORs in a 5-minute window → SNS.
        # NOT_BREACHING on missing data so quiet periods don't false-alarm.
        alarm = cloudwatch.Alarm(
            self, "UpstreamErrorAlarm",
            alarm_description="Upstream errors (AWS Location/Celestrak/7Timer) in last 5 min",
            metric=cloudwatch.Metric(
                namespace="PyNightSky",
                metric_name="UpstreamErrors",
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=3,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # WAF request logging (deferred from M7.2). Log group name MUST start with
        # "aws-waf-logs-" for WAF→CloudWatch Logs delivery. A resource policy grants the
        # delivery.logs service principal write access to the log streams under it.
        waf_log_group = logs.LogGroup(
            self, "WafLogs",
            log_group_name="aws-waf-logs-pynightsky",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        logs.ResourcePolicy(
            self, "WafLogsPolicy",
            resource_policy_name="pynightsky-waf-logs-policy",
            policy_statements=[
                iam.PolicyStatement(
                    principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[f"{waf_log_group.log_group_arn}:*"],
                    conditions={"StringEquals": {"aws:SourceAccount": self.account}},
                )
            ],
        )
        wafv2.CfnLoggingConfiguration(
            self, "WafLogging",
            log_destination_configs=[waf_log_group.log_group_arn],
            resource_arn=web_acl.attr_arn,
        )

        # Route 53 A-alias: darkhours.app → CloudFront distribution (no TTL, free).
        route53.ARecord(
            self, "AliasRecord",
            zone=hosted_zone,
            target=route53.RecordTarget.from_alias(
                route53_targets.CloudFrontTarget(dist)
            ),
        )

        CfnOutput(self, "DomainUrl", value="https://darkhours.app")
        CfnOutput(self, "CloudFrontUrl", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "LambdaFunctionName", value=fn.function_name)
        CfnOutput(self, "SpaBucketName", value=spa_bucket.bucket_name)
        CfnOutput(self, "WebAclArn", value=web_acl.attr_arn)
        CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)
        CfnOutput(self, "WafLogGroupName", value=waf_log_group.log_group_name)
