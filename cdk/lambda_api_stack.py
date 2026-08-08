"""Lambda + CloudFront deployment of the PyNightSky API.

The API runs as a zip Lambda (Mangum ASGI adapter, same CDK asset-bundling pattern as
the worker). Its Function URL uses AWS_IAM auth because the account SCP blocks public
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

# The repo whose CI publishes the /dark-sky/* destination pages. A GitHub repo slug is
# public identity, not a secret, so it lives in source (same reasoning as CicdStack's
# GITHUB_REPO). The numeric IDs are needed for GitHub's immutable-subject-claim format —
# see the trust policy below and CicdStack's module docstring. `gh api repos/<owner>/<repo>`
# prints both.
_DESTINATIONS_REPO = os.environ.get(
    "DESTINATIONS_GITHUB_REPO", "mbeher2200/darkhours-destinations",
)
_DESTINATIONS_OWNER_ID = os.environ.get("DESTINATIONS_GITHUB_OWNER_ID", "191662990")
_DESTINATIONS_REPO_ID = os.environ.get("DESTINATIONS_GITHUB_REPO_ID", "1314433826")


def _stage_worker_src() -> str:
    """Stage the minimal source the worker zip needs as the bundling input.

    The runtime is GDAL/scipy/pyarrow-free (light-pollution rasters are read from S3
    as raw-binary grids with numpy+boto3; the PAD-US index is a numpy .npz), so the
    worker fits a zip Lambda (~169 MB unzipped < 250 MB). We ship the engine source,
    apps (minus the web SPA), the de421.bsp ephemeris (inside darkhours/),
    the PAD-US + OSM-POI .npz indexes, and requirements.txt; bundling pip-installs the
    deps on top.
    """
    stage = _REPO / "cdk" / ".worker_build"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ig = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules", "web", "dist")
    shutil.copytree(_REPO / "darkhours", stage / "darkhours", ignore=ig)
    shutil.copytree(_REPO / "apps", stage / "apps", ignore=ig)
    (stage / "cache").mkdir()
    for _npz in ("darkhours_padus_h3.npz", "osm_pois.npz", "lightdome_h3.npz"):
        shutil.copy(_REPO / "cache" / _npz, stage / "cache" / _npz)
    shutil.copy(_REPO / "requirements.txt", stage / "requirements.txt")
    return str(stage)


def _stage_api_src() -> str:
    """Stage the source the API zip Lambda needs as the CDK bundling input.

    Stages engine + apps (minus worker/warmer entrypoints), de421.bsp ephemeris, and
    the light-dome + PAD-US .npz indexes. Omits osm_pois.npz (only find_nearby /
    worker uses it). requirements-api.txt is installed by CDK bundling on top.
    """
    stage = _REPO / "cdk" / ".api_build"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ig = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules", "web", "dist")
    shutil.copytree(_REPO / "darkhours", stage / "darkhours", ignore=ig)
    shutil.copytree(_REPO / "apps", stage / "apps", ignore=ig)
    (stage / "cache").mkdir()
    for _npz in ("darkhours_padus_h3.npz", "lightdome_h3.npz"):
        shutil.copy(_REPO / "cache" / _npz, stage / "cache" / _npz)
    # requirements-api.txt has `-r requirements.txt`; both must be present for pip.
    shutil.copy(_REPO / "requirements.txt", stage / "requirements.txt")
    shutil.copy(_REPO / "requirements-api.txt", stage / "requirements-api.txt")
    return str(stage)


class LambdaApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        raster_bucket = os.environ["PYNIGHTSKY_RASTER_BUCKET"]
        cache_table = os.environ["PYNIGHTSKY_CACHE_TABLE"]
        blog_bucket_name = os.environ["PYNIGHTSKY_BLOG_BUCKET"]
        # Optional (not a hard deploy dependency): aqicn.py degrades to "no live haze
        # data" when unset, so a missing secret shouldn't break the stack.
        aqicn_token = os.environ.get("AQICN_TOKEN", "")
        # Optional, and deliberately NOT a CloudFormation export/import: that would
        # couple this CI-deployed stack's deploy to the manually-deployed
        # PyNightSkyProviderHealth stack. Passed as a plain env var/secret instead
        # (see docs/CIRCUIT_BREAKER.md "Monitor-driven recovery wiring"); circuit_breaker.py
        # already degrades to self-timed recovery when this is unset.
        provider_health_table = os.environ.get("PYNIGHTSKY_PROVIDER_HEALTH_TABLE", "").strip()

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

        # --- the API as a zip Lambda (Mangum ASGI adapter, same pattern as the worker) ---
        # CDK asset bundling pip-installs requirements-api.txt on linux/arm64 during
        # `cdk deploy`; no Docker build or ECR push needed in CI.
        fn = lambda_.Function(
            self, "Api",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="apps.api.handler.handler",
            code=lambda_.Code.from_asset(
                _stage_api_src(),
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    platform="linux/arm64",
                    command=[
                        "bash", "-c",
                        "pip install --no-cache-dir -r requirements-api.txt "
                        "-t /asset-output "
                        "&& cp -r darkhours apps cache /asset-output/",
                    ],
                ),
            ),
            memory_size=3008,
            timeout=Duration.seconds(120),
            tracing=lambda_.Tracing.ACTIVE,
            log_group=api_log_group,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
                "LOG_LEVEL": "INFO",
                "AQICN_TOKEN": aqicn_token,
            },
        )

        # --- Scheduled warmup ping — keeps one container alive, prevents cold starts ---
        # EventBridge invokes Lambda directly every 4 minutes with a synthetic Function URL
        # v2.0 payload so Mangum can infer the HTTPGateway handler (raw EB events have no
        # requestContext and crash Mangum before any code runs).
        # Cost: ~10,800 invocations/month + ~1,620 GB-s — both within Lambda free tier.
        warmup_rule = events.Rule(
            self, "WarmupRule",
            schedule=events.Schedule.rate(Duration.minutes(4)),
        )
        warmup_rule.add_target(targets.LambdaFunction(
            fn,
            event=events.RuleTargetInput.from_object({
                "version": "2.0",
                "routeKey": "POST /warmup",
                "rawPath": "/warmup",
                "rawQueryString": "",
                "headers": {
                    "host": "warmup.internal",
                    "x-forwarded-proto": "https",
                },
                "requestContext": {
                    "accountId": "warmup",
                    "apiId": "warmup",
                    "domainName": "warmup.internal",
                    "http": {
                        "method": "POST",
                        "path": "/warmup",
                        "protocol": "HTTP/1.1",
                        "sourceIp": "127.0.0.1",
                        "userAgent": "EventBridge/warmup",
                    },
                    "requestId": "warmup",
                    "routeKey": "$default",
                    "stage": "$default",
                    "timeEpoch": 0,
                },
                "isBase64Encoded": False,
            }),
        ))

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
            actions=[
                "geo:SearchPlaceIndexForText",        # forward geocode (resolve)
                "geo:SearchPlaceIndexForPosition",    # reverse geocode (coords → name)
                "geo:SearchPlaceIndexForSuggestions", # /suggest autocomplete
            ],
            resources=[place_index_arn],
        )
        fn.add_to_role_policy(geo_policy)
        fn.add_environment("PYNIGHTSKY_PLACE_INDEX", place_index.index_name)

        # Drive times use Amazon Location GeoRoutes CalculateRoutes (point-to-point) — the
        # resource-less routing API (no route calculator to create). NOT the batched
        # CalculateRouteMatrix: that endpoint's response has no Notices/Legs, so it can't
        # detect a ferry-bridged or unpaved-road route (see darksky._aws_route_one). The
        # action takes no resource ARN, so it's scoped to "*".
        route_policy = iam.PolicyStatement(
            actions=["geo-routes:CalculateRoutes"],
            resources=["*"],
        )
        fn.add_to_role_policy(route_policy)

        # --- async jobs: SQS queue + zip-Lambda worker (M6.3) ---
        # Long async computes (/nearby, /calendar) run off the request path. The API
        # enqueues a job; this worker runs find_nearby or plan_trip and writes the
        # result into the cache, where /jobs/{id} reads it. visibility_timeout must
        # exceed the worker timeout; a DLQ catches messages that can't be processed.
        #
        # The worker is a zip Lambda (not a container): with rasterio/GDAL, pyarrow and
        # scipy removed from the runtime, the deps fit the 250 MB zip limit (~169 MB
        # unzipped), which avoids the container image's first-invoke image-load latency.
        # Deps are pip-installed for the Lambda runtime (linux/arm64) by CDK bundling.
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
            architecture=lambda_.Architecture.ARM_64,
            handler="apps.worker.handler.handler",
            code=lambda_.Code.from_asset(
                _stage_worker_src(),
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    platform="linux/arm64",
                    command=[
                        "bash", "-c",
                        "pip install --no-cache-dir -r requirements.txt "
                        "python-json-logger aws-xray-sdk -t /asset-output "
                        "&& cp -r darkhours apps cache /asset-output/",
                    ],
                ),
            ),
            memory_size=3008,
            timeout=Duration.seconds(900),                # 15 min: large multi-night trips
            tracing=lambda_.Tracing.ACTIVE,
            log_group=worker_log_group,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
                "LOG_LEVEL": "INFO",
                "AQICN_TOKEN": aqicn_token,
            },
        )
        bucket.grant_read(worker)
        table.grant_read_write_data(worker)
        worker.add_to_role_policy(geo_policy)
        worker.add_environment("PYNIGHTSKY_PLACE_INDEX", place_index.index_name)
        worker.add_to_role_policy(route_policy)
        # max_concurrency caps how many worker invocations SQS drives in parallel. Without
        # it, a backlog of jobs can hold every slot in the account-wide Lambda concurrency
        # pool for up to the worker's 900s timeout, throttling the API into user-facing
        # 5xx — the API and the worker draw from the same pool. This is sized against that
        # pool (currently 10), not against job throughput, so it is deliberately low:
        # raise it once the account's concurrent-executions quota is raised, or jobs will
        # queue rather than fan out. See docs/OBSERVABILITY.md § Capacity limits.
        worker.add_event_source(
            lambda_events.SqsEventSource(jobs_queue, batch_size=1, max_concurrency=5)
        )

        # --- Provider health monitor read (circuit breaker's monitor-driven recovery) ---
        # Least-privilege on purpose: dynamodb:GetItem only (the breaker does single-key
        # lookups, never Query/Scan), scoped to this one table's ARN. Referenced by name
        # only — imported via from_table_name so this stack takes no CDK dependency on
        # PyNightSkyProviderHealth (see the provider_health_table comment above). If the
        # env var/secret is unset, no grant or env var is added and every provider falls
        # back to self-timed recovery, exactly like today.
        if provider_health_table:
            health_table = dynamodb.Table.from_table_name(
                self, "ProviderHealthTable", provider_health_table,
            )
            health_read_policy = iam.PolicyStatement(
                actions=["dynamodb:GetItem"],
                resources=[health_table.table_arn],
            )
            for lambda_fn in (fn, worker):
                lambda_fn.add_to_role_policy(health_read_policy)
                lambda_fn.add_environment("PYNIGHTSKY_PROVIDER_HEALTH_TABLE", provider_health_table)

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

        # The async endpoints must NOT be cached: /nearby and /jobs return a fresh
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

        # --- Response header hardening ---
        # Two policies, not one: CSP is the one header that can actually break a page if
        # it's wrong, and the /blog* origin is a separately-deployed Astro site (its own
        # repo/CI — see the blog_bucket comment below) whose inline-script/style needs
        # aren't auditable from here. So the SPA behavior gets the full policy including
        # CSP (audited against apps/web/src: Plausible, the same-origin API, AWS RUM's
        # dataplane endpoint, Google Fonts, and the data: URI the red-mode favicon swap
        # uses); everything else gets the headers that can't break a response no matter
        # its content.
        _base_security_headers = cloudfront.ResponseSecurityHeadersBehavior(
            content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
            frame_options=cloudfront.ResponseHeadersFrameOptions(
                frame_option=cloudfront.HeadersFrameOption.DENY, override=True,
            ),
            referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                override=True,
            ),
            # includeSubDomains is safe now that *.darkhours.app has its own valid cert
            # (the wildcard redirect distribution) — every subdomain actually serves HTTPS.
            # preload is deliberately left off: submitting to the browser preload list is
            # a one-way door (see hstspreload.org), not something to opt into in passing.
            strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                access_control_max_age=Duration.days(365),
                include_subdomains=True,
                override=True,
            ),
        )
        base_headers_policy = cloudfront.ResponseHeadersPolicy(
            self, "BaseSecurityHeaders",
            comment="Security headers safe for any response (no CSP)",
            security_headers_behavior=_base_security_headers,
        )
        spa_headers_policy = cloudfront.ResponseHeadersPolicy(
            self, "SpaSecurityHeaders",
            comment="Security headers + CSP for the SPA document/assets",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_type_options=_base_security_headers.content_type_options,
                frame_options=_base_security_headers.frame_options,
                referrer_policy=_base_security_headers.referrer_policy,
                strict_transport_security=_base_security_headers.strict_transport_security,
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=(
                        "default-src 'self'; "
                        "script-src 'self' https://plausible.io; "
                        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                        "font-src 'self' https://fonts.gstatic.com; "
                        "img-src 'self' data:; "
                        "connect-src 'self' https://plausible.io "
                        "https://dataplane.rum.us-east-1.amazonaws.com; "
                        "object-src 'none'; base-uri 'none'; form-action 'self'; "
                        "frame-ancestors 'none'"
                    ),
                    override=True,
                ),
            ),
            # Geolocation powers the "use my location" search; nothing else is used.
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[cloudfront.ResponseCustomHeader(
                    header="Permissions-Policy",
                    value="geolocation=(self), camera=(), microphone=(), payment=()",
                    override=True,
                )],
            ),
        )

        no_cache = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=fwd_qs,
            function_associations=_sec_fetch,
            response_headers_policy=base_headers_policy,
        )
        # /night: cached GETs keyed on the full query string, same-origin guard applied.
        api_cached = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=api_cache,
            function_associations=_sec_fetch,
            response_headers_policy=base_headers_policy,
        )
        # /healthz: same cache policy, no guard so uptime monitors can reach it.
        open_cached = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=api_cache,
            response_headers_policy=base_headers_policy,
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
        #   2  CommonRuleSet           — the WAF "Core Rule Set": broad generic-attack coverage
        #                                (XSS, path traversal, LFI/RFI, oversized bodies, etc.,
        #                                ~700 WCU). COUNT, not BLOCK, for now — one of its rules
        #                                (NoUserAgent_HEADER) blocks requests with no User-Agent,
        #                                which some uptime monitors on /healthz omit, and that's
        #                                not something to find out by breaking prod. Watch the
        #                                WAF logs (aws-waf-logs-pynightsky, see M7.3 below) for a
        #                                stretch with no unexpected COUNTs, then flip
        #                                override_action to none.
        #   3  RateLimitNearbyPerIp    — block any IP exceeding 10 /nearby requests / 5 min.
        #                                Each /nearby spawns an SQS job; this caps worker cost.
        #   4  RateLimitCalendarPerIp  — block any IP exceeding 30 /calendar requests / 5 min.
        #                                One /calendar call = one SQS job, bounded to <=30
        #                                location-nights (_MAX_CALENDAR_DAYS). The web client
        #                                now auto-fires this once per distinct location viewed
        #                                (not just on manual range-picker use), so the limit is
        #                                sized for someone actively comparing several spots in
        #                                one session rather than a single opt-in click.
        #   5  RateLimitPerIp          — block any single IP exceeding 150 requests / 5 min
        #                                across the API surface (scoped: /night, /suggest,
        #                                /nearby, /calendar, /jobs, /healthz). Raised alongside
        #                                the calendar auto-fire above: each location view now
        #                                costs ~1 /night + 1 /calendar submit + a few
        #                                /jobs/{id} polls (polls aren't covered by the
        #                                per-endpoint rules, only this one), plus typeahead
        #                                /suggest calls. Still well below anything a scripted
        #                                scraper needs seconds, not minutes, to exceed.
        #                                The scope-down matters: while this rule counted
        #                                static assets too, real sessions were tripping it on
        #                                favicons and bundle fetches and getting 403s on the
        #                                API calls that followed.
        # Total ~931 WCU, still under the 1500-WCU default WebACL capacity (rate-based rule cost
        # doesn't scale with the numeric limit). Managed groups use override_action=none so each
        # group's own block/count actions apply unchanged — except CommonRuleSet, see above.
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
                    name="CommonRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(count={}),
                    statement=managed("AWSManagedRulesCommonRuleSet"),
                    visibility_config=vis("CommonRuleSet"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitNearbyPerIp",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=10,                  # /nearby per IP per 5-min window
                            aggregate_key_type="IP",
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                                    positional_constraint="STARTS_WITH",
                                    search_string="/nearby",
                                    text_transformations=[wafv2.CfnWebACL.TextTransformationProperty(
                                        priority=0, type="NONE",
                                    )],
                                ),
                            ),
                        ),
                    ),
                    visibility_config=vis("RateLimitNearbyPerIp"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitCalendarPerIp",
                    priority=4,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=30,                  # /calendar per IP per 5-min window
                            aggregate_key_type="IP",
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                                    positional_constraint="STARTS_WITH",
                                    search_string="/calendar",
                                    text_transformations=[wafv2.CfnWebACL.TextTransformationProperty(
                                        priority=0, type="NONE",
                                    )],
                                ),
                            ),
                        ),
                    ),
                    visibility_config=vis("RateLimitCalendarPerIp"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitPerIp",
                    priority=5,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=150,                 # API requests per IP per 5-min window
                            aggregate_key_type="IP",
                            # Scoped to the API surface. Unscoped, this counted every asset
                            # CloudFront served — favicons, images, the JS bundle — so a
                            # single engaged session could exhaust 150 on static files alone
                            # and get 403s on the API calls that matter. Static assets are
                            # served from S3 and cost nothing to serve; only the Lambda-backed
                            # paths are worth rate limiting. Mirrors the STARTS_WITH style of
                            # the two per-endpoint rules above.
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                or_statement=wafv2.CfnWebACL.OrStatementProperty(
                                    statements=[
                                        wafv2.CfnWebACL.StatementProperty(
                                            byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                                field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                                                positional_constraint="STARTS_WITH",
                                                search_string=path,
                                                text_transformations=[wafv2.CfnWebACL.TextTransformationProperty(
                                                    priority=0, type="NONE",
                                                )],
                                            ),
                                        )
                                        for path in (
                                            "/night", "/suggest", "/nearby",
                                            "/calendar", "/jobs", "/healthz",
                                        )
                                    ],
                                ),
                            ),
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

        # --- Blog S3 origin ---
        # The darkhours-blog repo deploys to this bucket via its own CI (s3 sync).
        # We import it here so CDK keeps the /blog* behavior alive on every redeploy.
        # The bucket policy already grants this distribution OAC read access.
        blog_bucket = s3.Bucket.from_bucket_name(
            self, "BlogBucket",
            blog_bucket_name,
        )
        blog_origin = origins.S3BucketOrigin.with_origin_access_control(blog_bucket)

        # Existing CloudFront Function (created outside CDK) that rewrites directory
        # requests to index.html so Astro's static output is served correctly from S3.
        blog_rewrite_fn = cloudfront.Function.from_function_attributes(
            self, "BlogIndexRewriteFn",
            function_arn=f"arn:aws:cloudfront::{self.account}:function/AstroBlogIndexRewrite",
            function_name="AstroBlogIndexRewrite",
        )
        blog_fn_assoc = [cloudfront.FunctionAssociation(
            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
            function=blog_rewrite_fn,
        )]

        # --- Dark-sky destination pages: S3 origin for the darkhours-destinations repo ---
        # Static per-destination guides (SEO landing pages) served at /dark-sky/*. Like the
        # blog, the content lives in its own repo with its own CI (s3 sync + invalidation);
        # unlike the blog, the bucket is CREATED here rather than imported by name. That is
        # deliberate: with a CDK-managed bucket, S3BucketOrigin.with_origin_access_control()
        # writes the OAC bucket policy automatically, so there is no manual policy step and
        # no chicken-and-egg between "bucket needs the distribution ARN" and "distribution
        # needs the bucket name". (The blog bucket predates this stack, which is the only
        # reason it is imported and its policy was applied by hand.)
        #
        # A CloudFront distribution's behaviors cannot be split across stacks, so the
        # /dark-sky* behavior has to live here regardless — keeping its origin here too
        # means one origin's config is in one place instead of spread across two repos.
        #
        # RETAIN + no auto_delete_objects (contrast spa_bucket above): these objects are
        # published by a different repo's pipeline and cannot be rebuilt from this source
        # tree, so they must never be collateral damage of a stack teardown.
        dest_bucket = s3.Bucket(
            self, "DestinationsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        dest_origin = origins.S3BucketOrigin.with_origin_access_control(dest_bucket)

        # Directory-index rewrite for the statically generated pages. Written inline (not
        # created out-of-band like AstroBlogIndexRewrite) because it has to stay in lockstep
        # with the generator's output, so it belongs versioned with this stack.
        #
        #   /dark-sky/          -> /dark-sky/index.html
        #   /dark-sky/foo/      -> /dark-sky/foo/index.html
        #   /dark-sky/foo       -> 301 /dark-sky/foo/          (query string preserved)
        #   /dark-sky/_astro/x.js, /dark-sky/index.json -> untouched (dot in last segment)
        #
        # The 301 rather than a silent rewrite is an SEO choice: serving 200 on both the
        # slashed and unslashed form creates two URLs for one page and makes Google dedupe
        # them via rel=canonical. Redirecting removes the ambiguity at the edge instead.
        # This function is only associated with /dark-sky*, so it never sees SPA or API paths.
        _dark_sky_rewrite_js = """
function handler(event) {
  var req = event.request;
  var uri = req.uri;

  if (uri.endsWith('/')) {
    req.uri = uri + 'index.html';
    return req;
  }

  var last = uri.substring(uri.lastIndexOf('/') + 1);
  if (last.indexOf('.') !== -1) {
    return req;
  }

  var qs = '';
  var q = req.querystring;
  for (var k in q) {
    var ek = encodeURIComponent(k);
    if (q[k].multiValue) {
      for (var i = 0; i < q[k].multiValue.length; i++) {
        qs += (qs ? '&' : '?') + ek + '=' + encodeURIComponent(q[k].multiValue[i].value);
      }
    } else {
      qs += (qs ? '&' : '?') + ek + (q[k].value ? '=' + encodeURIComponent(q[k].value) : '');
    }
  }

  return {
    statusCode: 301,
    statusDescription: 'Moved Permanently',
    headers: { 'location': { value: uri + '/' + qs } }
  };
}
"""
        dark_sky_rewrite_fn = cloudfront.Function(
            self, "DarkSkyIndexRewrite",
            code=cloudfront.FunctionCode.from_inline(_dark_sky_rewrite_js),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )
        dark_sky_fn_assoc = [cloudfront.FunctionAssociation(
            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
            function=dark_sky_rewrite_fn,
        )]

        # --- Access logs (standard logs) to S3 ---
        # Free from CloudFront itself; only S3 storage/request costs apply, which at this
        # traffic volume round to a few cents/month. 30-day expiry keeps it near-zero
        # indefinitely — these are for short-term forensics (e.g. telling real visitors
        # apart from bot/scanner spikes), not long-term retention. object_ownership must
        # be OBJECT_WRITER: CloudFront's legacy standard logging delivers via a canned ACL.
        log_bucket = s3.Bucket(
            self, "AccessLogBucket",
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
        )

        dist = cloudfront.Distribution(
            self, "Cdn",
            comment="PyNightSky SPA + API (S3 default, Lambda for API paths)",
            default_root_object="index.html",
            web_acl_id=web_acl.attr_arn,
            domain_names=["darkhours.app"],
            certificate=cert,
            enable_logging=True,
            log_bucket=log_bucket,
            log_file_prefix="standard/",
            default_behavior=cloudfront.BehaviorOptions(
                origin=spa_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=spa_headers_policy,
            ),
            additional_behaviors={
                "/night":    api_cached,
                "/suggest":  api_cached,   # autocomplete: edge-cache by query string
                "/healthz":  open_cached,
                "/nearby":   no_cache,
                "/calendar": no_cache,
                "/jobs/*":   no_cache,
                "/blog*":    cloudfront.BehaviorOptions(
                    origin=blog_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    function_associations=blog_fn_assoc,
                    response_headers_policy=base_headers_policy,
                ),
                # base_headers_policy (no CSP) for now, matching /blog*. The destination
                # pages are ours and will get a dedicated CSP once the static generator's
                # real output is known — writing one blind risks blocking the hydration
                # script of whatever the build emits, which is exactly the "CSP is the one
                # header that can actually break a page" hazard called out above. Tighten
                # this after the first content deploy, verified against the built HTML.
                "/dark-sky*": cloudfront.BehaviorOptions(
                    origin=dest_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    function_associations=dark_sky_fn_assoc,
                    response_headers_policy=base_headers_policy,
                ),
            },
        )

        # with_origin_access_control() above only grants s3:GetObject, so a request for a
        # /dark-sky/* key that doesn't exist (typo'd URL, not-yet-published destination
        # page, stray /dark-sky/sitemap.xml probe) comes back as 403 rather than 404 — S3
        # withholds the "not found" distinction from a principal that can't list the
        # bucket. Googlebot then logs it as "blocked" instead of "not found", which is a
        # worse Search Console signal. Granting ListBucket (on the bucket itself, not
        # bucket/*) to the same OAC-scoped principal is the standard fix and only affects
        # this one bucket of public static pages — deliberately not done via a
        # distribution-level error_responses 403->404 remap, since that config applies to
        # every behavior on `dist` and would also mask real 403s from the WAF rate-limit
        # rules on /nearby, /calendar, and the site overall.
        dest_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudFrontServicePrincipalListBucket",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                actions=["s3:ListBucket"],
                resources=[dest_bucket.bucket_arn],
                conditions={
                    "StringEquals": {
                        "AWS:SourceArn": (
                            f"arn:aws:cloudfront::{self.account}:"
                            f"distribution/{dist.distribution_id}"
                        ),
                    },
                },
            )
        )

        # Upload the built SPA and invalidate the edge cache on every deploy. The asset
        # path is resolved relative to this file so it works regardless of cwd.
        #
        # Split into two deployments so *.html gets an explicit charset: S3's default
        # content-type guesser (same one `aws s3 sync` uses) sets `text/html` with no
        # charset param, which makes any consumer that trusts the HTTP header instead of
        # sniffing the document (Python `requests`, many bots/aggregators, unfurl tools)
        # mis-decode non-ASCII bytes as ISO-8859-1 — mirrors the fix already shipped for
        # the /dark-sky/* pipeline (darkhours-destinations repo). The non-html pass keeps
        # `prune` (its default) so stale assets still get removed; the html-only pass
        # must NOT prune, or it would delete every non-html key it doesn't know about.
        #
        # Each pass also sets Cache-Control, which nothing here did before: objects landed
        # with no cache header at all, so browsers fell back to heuristic freshness off
        # Last-Modified — and Last-Modified is the deploy timestamp, so the heuristic window
        # was ~nothing and every navigation revalidated every asset. In the WAF logs a single
        # session showed 146 favicon round-trips in one hour for this reason. Three tiers:
        #
        #   assets/*   content-hashed by Vite (index-<hash>.js), so the URL changes whenever
        #              the bytes do -> immutable, cache for a year, never revalidate.
        #   other      favicons, images, *.bin, robots/sitemap: stable names, so a fixed TTL.
        #              One day is the trade: a returning browser can be up to a day stale on
        #              these after a deploy, which is fine for content that rarely changes.
        #   *.html     the entry point that names the current asset hashes. no-cache means
        #              "store it, but revalidate every time" -> cheap 304s, and a deploy is
        #              picked up on the next navigation instead of a day later.
        #
        # Pruning stays scoped per pass: assets/* prunes within itself (so superseded
        # bundles don't accumulate), the non-html pass prunes everything it owns, and the
        # html pass must NOT prune or it would delete every non-html key it doesn't know.
        spa_dist_path = os.path.join(os.path.dirname(__file__), "..", "apps", "web", "dist")
        s3deploy.BucketDeployment(
            self, "SpaDeployAssets",
            sources=[s3deploy.Source.asset(spa_dist_path)],
            destination_bucket=spa_bucket,
            exclude=["*"],
            include=["assets/*"],
            cache_control=[s3deploy.CacheControl.from_string(
                "public, max-age=31536000, immutable",
            )],
            distribution=dist,
            distribution_paths=["/*"],
        )
        s3deploy.BucketDeployment(
            self, "SpaDeploy",
            sources=[s3deploy.Source.asset(spa_dist_path)],
            destination_bucket=spa_bucket,
            exclude=["*.html", "assets/*"],
            cache_control=[s3deploy.CacheControl.from_string("public, max-age=86400")],
            distribution=dist,
            distribution_paths=["/*"],
        )
        s3deploy.BucketDeployment(
            self, "SpaDeployHtml",
            sources=[s3deploy.Source.asset(spa_dist_path)],
            destination_bucket=spa_bucket,
            exclude=["*"],
            include=["*.html"],
            content_type="text/html; charset=utf-8",
            cache_control=[s3deploy.CacheControl.from_string("no-cache")],
            prune=False,
            distribution=dist,
            distribution_paths=["/*"],
        )

        # --- Keyless deploy role for the darkhours-destinations repo ---
        # That repo's CI publishes the /dark-sky/* pages: `aws s3 sync` into the bucket
        # above, then one invalidation. It never runs CDK and owns no AWS resources, so
        # this role is all the access it needs — no S3 read of anything else, no
        # CloudFormation, no bootstrap-role assumption (contrast the deploy role in
        # CicdStack, which does deploy stacks).
        #
        # The account's GitHub OIDC provider already exists (created by CicdStack); there
        # can only be one per URL per account, so import it by its deterministic ARN rather
        # than declaring a second one — this is the fallback CicdStack's own comment points
        # at. Importing also keeps this role next to the bucket and distribution it grants
        # on, so there is no cross-stack export to keep in sync.
        gh_oidc_provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self, "GitHubOidcImported",
            f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com",
        )

        # Both subject formats are accepted because GitHub's immutable subject claims
        # (see CicdStack's module docstring) qualify the `sub` with numeric owner/repo IDs
        # for repos renamed or transferred after 2026-07-15, and it is not worth a failed
        # first deploy to guess which form a freshly-created repo emits. Both patterns are
        # pinned to this exact owner+repo and to refs/heads/main, so accepting the pair
        # widens nothing: a different repo matches neither.
        _dest_owner, _dest_name = _DESTINATIONS_REPO.split("/")
        _dest_allowed_subs = [
            f"repo:{_dest_owner}/{_dest_name}:ref:refs/heads/main",
            f"repo:{_dest_owner}@{_DESTINATIONS_OWNER_ID}/"
            f"{_dest_name}@{_DESTINATIONS_REPO_ID}:ref:refs/heads/main",
        ]
        dest_deploy_role = iam.Role(
            self, "DestinationsDeployRole",
            role_name="darkhours-destinations-deploy",
            description=(
                "Assumed by the darkhours-destinations GitHub Actions workflow (main "
                "branch) to publish the /dark-sky/* static pages."
            ),
            assumed_by=iam.WebIdentityPrincipal(
                gh_oidc_provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": _dest_allowed_subs,
                    },
                },
            ),
        )
        # `s3 sync --delete` needs List (to diff), Put (to upload) and Delete (to prune);
        # grant_read_write covers all three (its action list already includes
        # s3:DeleteObject*), plus Get so sync compares instead of re-uploading everything.
        dest_bucket.grant_read_write(dest_deploy_role)
        # CreateInvalidation is scoped to this one distribution.
        dest_deploy_role.add_to_policy(iam.PolicyStatement(
            sid="InvalidateDarkSkyPaths",
            actions=["cloudfront:CreateInvalidation"],
            resources=[
                f"arn:aws:cloudfront::{self.account}:distribution/{dist.distribution_id}",
            ],
        ))

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
        #   aws sns subscribe --topic-arn <AlarmTopicArn> \
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

        # --- M8: observability dashboard + real alarm wiring ---
        # AWS Application Insights (console-enabled, outside CDK) auto-generated ~20 alarms
        # covering Lambda/DynamoDB/SQS, but every one has empty AlarmActions — they evaluate
        # and sit there, notifying nobody. Rather than reach into resources CDK doesn't own,
        # add a focused set of real alarms here, wired to the alarm_topic that already exists
        # (M7.3) but had zero subscribers — subscribe an email to it after this deploys:
        #   aws sns subscribe --region us-east-1 \
        #     --topic-arn <AlarmTopicArn output> --protocol email --notification-endpoint <you>
        api_errors_alarm = cloudwatch.Alarm(
            self, "ApiErrorsAlarm",
            alarm_description="API Lambda errors in the last 5 minutes",
            metric=fn.metric_errors(statistic="Sum", period=Duration.minutes(5)),
            threshold=3,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        api_errors_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        worker_errors_alarm = cloudwatch.Alarm(
            self, "WorkerErrorsAlarm",
            alarm_description="Worker Lambda errors in the last 5 minutes",
            metric=worker.metric_errors(statistic="Sum", period=Duration.minutes(5)),
            threshold=2,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        worker_errors_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # Highest-value new alarm: today the DLQ has zero live notification coverage. Any
        # message here means a job failed 3x (max_receive_count) and needs a human look.
        dlq_not_empty_alarm = cloudwatch.Alarm(
            self, "DlqNotEmptyAlarm",
            alarm_description="A job landed in the dead-letter queue",
            metric=dlq.metric_approximate_number_of_messages_visible(
                statistic="Maximum", period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        dlq_not_empty_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # Threshold is intentionally loose (20%, not the usual 1-5%): at this traffic volume
        # a handful of failed requests can swing the rate metric sharply. Tune down once a
        # week of real baseline is observed (see docs/OBSERVABILITY.md).
        cf_5xx_metric = cloudwatch.Metric(
            namespace="AWS/CloudFront",
            metric_name="5xxErrorRate",
            dimensions_map={"DistributionId": dist.distribution_id, "Region": "Global"},
            region="us-east-1",
            statistic="Average",
            period=Duration.minutes(5),
        )
        cloudfront_5xx_alarm = cloudwatch.Alarm(
            self, "CloudFront5xxErrorRateAlarm",
            alarm_description="CloudFront 5xx error rate elevated",
            metric=cf_5xx_metric,
            threshold=20,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        cloudfront_5xx_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        def _cf_metric(name: str, stat: str = "Sum") -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/CloudFront",
                metric_name=name,
                dimensions_map={"DistributionId": dist.distribution_id, "Region": "Global"},
                region="us-east-1",
                statistic=stat,
                period=Duration.minutes(5),
            )

        def _waf_metric(rule: str, metric_name: str = "BlockedRequests") -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/WAFV2",
                metric_name=metric_name,
                dimensions_map={"WebACL": "PyNightSkyWaf", "Rule": rule, "Region": "Global"},
                region="us-east-1",
                statistic="Sum",
                period=Duration.minutes(5),
            )

        dashboard = cloudwatch.Dashboard(self, "Dashboard", dashboard_name="PyNightSky-Overview")
        dashboard.add_widgets(
            cloudwatch.AlarmStatusWidget(
                title="Alarms",
                alarms=[
                    alarm, api_errors_alarm, worker_errors_alarm,
                    dlq_not_empty_alarm, cloudfront_5xx_alarm,
                ],
                width=24,
            ),
        )
        def _concurrent_executions(function_name) -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/Lambda", metric_name="ConcurrentExecutions",
                dimensions_map={"FunctionName": function_name}, statistic="Maximum",
            )

        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="API Lambda — traffic",
                left=[fn.metric_invocations(), fn.metric_errors(), fn.metric_throttles()],
            ),
            cloudwatch.GraphWidget(
                title="API Lambda — duration",
                left=[fn.metric_duration(statistic="p50"), fn.metric_duration(statistic="p99")],
            ),
            cloudwatch.GraphWidget(
                title="API Lambda — concurrency",
                left=[_concurrent_executions(fn.function_name)],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Worker Lambda — traffic",
                left=[worker.metric_invocations(), worker.metric_errors(), worker.metric_throttles()],
            ),
            cloudwatch.GraphWidget(
                title="Worker Lambda — duration",
                left=[worker.metric_duration(statistic="p50"), worker.metric_duration(statistic="p99")],
            ),
            cloudwatch.GraphWidget(
                title="Worker Lambda — concurrency",
                left=[_concurrent_executions(worker.function_name)],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="DynamoDB — capacity",
                left=[table.metric_consumed_read_capacity_units(), table.metric_consumed_write_capacity_units()],
            ),
            cloudwatch.GraphWidget(
                title="DynamoDB — throttles/errors",
                left=[table.metric_throttled_requests(), table.metric_user_errors()],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Jobs queue",
                left=[
                    jobs_queue.metric_approximate_number_of_messages_visible(),
                    jobs_queue.metric_approximate_age_of_oldest_message(),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Dead-letter queue depth",
                left=[dlq.metric_approximate_number_of_messages_visible()],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="CloudFront — requests & errors",
                left=[_cf_metric("Requests")],
                right=[_cf_metric("4xxErrorRate", "Average"), _cf_metric("5xxErrorRate", "Average")],
            ),
            cloudwatch.GraphWidget(
                title="CloudFront — latency & cache",
                left=[_cf_metric("OriginLatency", "Average")],
                right=[_cf_metric("CacheHitRate", "Average")],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="WAF — blocked requests by rule",
                left=[
                    _waf_metric("AmazonIpReputation"),
                    _waf_metric("KnownBadInputs"),
                    _waf_metric("RateLimitNearbyPerIp"),
                    _waf_metric("RateLimitCalendarPerIp"),
                    _waf_metric("RateLimitPerIp"),
                ],
            ),
            # CommonRuleSet runs in COUNT (not BLOCK) — this is what tells us whether it's
            # safe to flip to enforcing. Watch for a quiet stretch with no surprise counts.
            cloudwatch.GraphWidget(
                title="WAF — CommonRuleSet counted requests (not yet blocking)",
                left=[_waf_metric("CommonRuleSet", "CountedRequests")],
            ),
            cloudwatch.GraphWidget(
                title="Upstream errors (Location/Celestrak/7Timer)",
                left=[cloudwatch.Metric(
                    namespace="PyNightSky", metric_name="UpstreamErrors",
                    statistic="Sum", period=Duration.minutes(5),
                )],
            ),
        )

        # --- Real User Monitoring (CloudWatch RUM, "DarkHours.app" app monitor) ---
        # RUM is set up client-side in apps/web/src/rum.ts (own app monitor, own resource
        # policy) — not owned by this stack. Referencing its metrics here by namespace/
        # dimension needs no CDK cross-stack dependency; CloudWatch metrics are account-wide.
        def _rum_metric(name: str, stat: str = "Sum") -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/RUM", metric_name=name,
                dimensions_map={"application_name": "DarkHours.app"},
                statistic=stat, period=Duration.hours(1),
            )

        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="RUM — sessions & page views",
                left=[_rum_metric("SessionCount"), _rum_metric("PageViewCount")],
            ),
            cloudwatch.GraphWidget(
                title="RUM — errors per page view",
                left=[
                    _rum_metric("JsErrorCount"),
                    _rum_metric("Http4xxCountPerPageView", "Average"),
                    _rum_metric("Http5xxCountPerPageView", "Average"),
                ],
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="RUM — Largest Contentful Paint (ms)",
                left=[_rum_metric("WebVitalsLargestContentfulPaint", "Average")],
            ),
            cloudwatch.GraphWidget(
                title="RUM — Cumulative Layout Shift",
                left=[_rum_metric("WebVitalsCumulativeLayoutShift", "Average")],
            ),
        )

        # --- Weather provider health (PyNightSkyProviderHealth stack's EMF metrics) ---
        # Separate CDK stack; same account-wide-metrics reasoning as the RUM widgets above.
        def _provider_metric(name: str, provider: str | None = None, stat: str = "Sum") -> cloudwatch.Metric:
            dims = {"Provider": provider} if provider else {}
            return cloudwatch.Metric(
                namespace="PyNightSky/WeatherProviders", metric_name=name,
                dimensions_map=dims, statistic=stat, period=Duration.minutes(5),
            )

        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Weather provider status (1=up)",
                left=[
                    _provider_metric("ProviderUp", "open-meteo", "Minimum"),
                    _provider_metric("ProviderUp", "7timer", "Minimum"),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Weather provider latency & DB write failures",
                left=[_provider_metric("HTTPVerificationLatency", stat="Average")],
                right=[_provider_metric("DynamoDBWriteFailure")],
            ),
        )

        dashboard.add_widgets(
            cloudwatch.TextWidget(
                markdown=(
                    "### X-Ray traces\n"
                    "Trace-level latency isn't a native dashboard widget — see the "
                    f"[X-Ray Service Map](https://{self.region}.console.aws.amazon.com/"
                    f"cloudwatch/home?region={self.region}#xray:service-map) "
                    "(API + Worker Lambda are traced; the TLE warmer is not)."
                ),
                width=24,
                height=3,
            ),
        )

        # Route 53 A-alias: darkhours.app → CloudFront distribution (no TTL, free).
        route53.ARecord(
            self, "AliasRecord",
            zone=hosted_zone,
            target=route53.RecordTarget.from_alias(
                route53_targets.CloudFrontTarget(dist)
            ),
        )

        # --- Wildcard subdomain redirect: *.darkhours.app -> darkhours.app ---
        # Any subdomain (www, typos, an old blog host, whatever shows up) 301s to the
        # equivalent path on the apex. blog.darkhours.app is special-cased to land under
        # /blog, where the Astro blog actually lives (see the /blog* behavior on `dist`
        # above), instead of 404ing there. This lives on its OWN distribution/cert rather
        # than being bolted onto `dist`: CloudFront allows only one function per
        # (behavior, event type), and every behavior on `dist` already has one (the
        # same-origin check or the blog directory rewrite) — merging in a second
        # host-based check isn't an option, and this redirect only ever needs the Host
        # header, never the origin, so a tiny standalone distribution is simplest.
        wildcard_cert = acm.Certificate(
            self, "WildcardCert",
            domain_name="*.darkhours.app",
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )
        _redirect_js = (
            "function handler(event){"
            "var host=event.request.headers.host.value.toLowerCase();"
            "var uri=event.request.uri;"
            "var qs=event.request.querystring;"
            "var parts=[];"
            "for(var k in qs){"
            "var e=qs[k];"
            "if(e.multiValue){for(var i=0;i<e.multiValue.length;i++)"
            "parts.push(encodeURIComponent(k)+'='+encodeURIComponent(e.multiValue[i].value));}"
            "else{parts.push(encodeURIComponent(k)+'='+encodeURIComponent(e.value));}}"
            "var qstr=parts.length?('?'+parts.join('&')):'';"
            "var prefix=(host==='blog.darkhours.app')?'/blog':'';"
            "return{statusCode:301,statusDescription:'Moved Permanently',"
            "headers:{location:{value:'https://darkhours.app'+prefix+uri+qstr}}};"
            "}"
        )
        redirect_fn = cloudfront.Function(
            self, "WildcardRedirectFn",
            code=cloudfront.FunctionCode.from_inline(_redirect_js),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )
        redirect_dist = cloudfront.Distribution(
            self, "WildcardRedirectCdn",
            comment="Redirect *.darkhours.app -> darkhours.app (blog.* -> /blog)",
            # Same WebACL as the main distribution — no extra base/per-rule cost, since
            # that's billed per Web ACL, not per attached distribution. Only adds the
            # (tiny, expected) per-request evaluation cost for whatever hits this one.
            web_acl_id=web_acl.attr_arn,
            domain_names=["*.darkhours.app"],
            certificate=wildcard_cert,
            default_behavior=cloudfront.BehaviorOptions(
                # Never actually fetched — the function always returns a redirect before
                # the origin would be hit. Pointed at the apex anyway as a safe fallback.
                origin=origins.HttpOrigin("darkhours.app"),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=[cloudfront.FunctionAssociation(
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    function=redirect_fn,
                )],
            ),
        )
        # Route 53 wildcard A-alias: *.darkhours.app → the redirect distribution.
        route53.ARecord(
            self, "WildcardAliasRecord",
            zone=hosted_zone,
            record_name="*.darkhours.app",
            target=route53.RecordTarget.from_alias(
                route53_targets.CloudFrontTarget(redirect_dist)
            ),
        )

        CfnOutput(self, "DomainUrl", value="https://darkhours.app")
        CfnOutput(
            self, "WildcardRedirectCloudFrontUrl",
            value=f"https://{redirect_dist.distribution_domain_name}",
        )
        CfnOutput(self, "CloudFrontUrl", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "LambdaFunctionName", value=fn.function_name)
        CfnOutput(self, "SpaBucketName", value=spa_bucket.bucket_name)
        # The three values the darkhours-destinations repo needs as Actions secrets:
        # AWS_DEPLOY_ROLE_ARN, DESTINATIONS_BUCKET, CLOUDFRONT_DISTRIBUTION_ID.
        CfnOutput(self, "DestinationsBucketName", value=dest_bucket.bucket_name,
                  description="Set as the darkhours-destinations secret DESTINATIONS_BUCKET")
        CfnOutput(self, "DestinationsDeployRoleArn", value=dest_deploy_role.role_arn,
                  description="Set as the darkhours-destinations secret AWS_DEPLOY_ROLE_ARN")
        CfnOutput(self, "DistributionId", value=dist.distribution_id,
                  description="Set as the darkhours-destinations secret CLOUDFRONT_DISTRIBUTION_ID")
        CfnOutput(self, "WebAclArn", value=web_acl.attr_arn)
        CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)
        CfnOutput(self, "WafLogGroupName", value=waf_log_group.log_group_name)
        CfnOutput(
            self, "DashboardUrl",
            value=(
                f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
                f"?region={self.region}#dashboards:name={dashboard.dashboard_name}"
            ),
        )
