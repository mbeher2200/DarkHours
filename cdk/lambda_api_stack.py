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

from aws_cdk import (
    Stack,
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
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_sqs as sqs,
)
from constructs import Construct


class LambdaApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        raster_bucket = os.environ["PYNIGHTSKY_RASTER_BUCKET"]
        cache_table = os.environ["PYNIGHTSKY_CACHE_TABLE"]

        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Env", "prod")
        Tags.of(self).add("Component", "api")

        # --- the API as a container Lambda (from the ECR image) ---
        # CI passes the immutable git SHA via `-c imageTag=<sha>` so each deploy
        # references a NEW tag — otherwise the mutable ":lambda" tag leaves the CFN
        # template unchanged and CloudFormation won't pick up a rebuilt image.
        image_tag = self.node.try_get_context("imageTag") or "lambda"
        repo = ecr.Repository.from_repository_name(self, "Repo", "pynightsky-api")
        fn = lambda_.DockerImageFunction(
            self, "Api",
            code=lambda_.DockerImageCode.from_ecr(repo, tag_or_digest=image_tag),
            memory_size=2048,          # 2 GB ~= the App Runner sizing; ~1.2 vCPU
            timeout=Duration.seconds(120),
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
            },
        )

        # least-privilege data access (reference foundational resources)
        bucket = s3.Bucket.from_bucket_name(self, "RasterBucket", raster_bucket)
        table = dynamodb.Table.from_table_name(self, "CacheTable", cache_table)
        bucket.grant_read(fn)               # s3:GetObject on the raster bucket
        table.grant_read_write_data(fn)     # DynamoDB cache get/set/invalidate

        # --- async jobs: SQS queue + container-Lambda worker (M6.3) ---
        # Long /calendar+/trip computes run off the request path. The API enqueues a
        # job; this worker (a container Lambda — it needs rasterio) runs plan_trip and
        # writes the result into the cache, where /jobs/{id} reads it. visibility_timeout
        # must exceed the worker timeout; a DLQ catches messages that can't be processed.
        dlq = sqs.Queue(self, "JobsDlq", retention_period=Duration.days(14))
        jobs_queue = sqs.Queue(
            self, "JobsQueue",
            visibility_timeout=Duration.seconds(960),     # > worker timeout (900s)
            retention_period=Duration.days(1),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )

        worker_tag = self.node.try_get_context("imageTag") or "worker"
        worker_repo = ecr.Repository.from_repository_name(self, "WorkerRepo", "pynightsky-worker")
        worker = lambda_.DockerImageFunction(
            self, "Worker",
            code=lambda_.DockerImageCode.from_ecr(worker_repo, tag_or_digest=worker_tag),
            memory_size=2048,
            timeout=Duration.seconds(900),                # 15 min: large multi-night trips
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
                "PYNIGHTSKY_RASTER_BUCKET": raster_bucket,
            },
        )
        bucket.grant_read(worker)
        table.grant_read_write_data(worker)
        worker.add_event_source(lambda_events.SqsEventSource(jobs_queue, batch_size=1))

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
        no_cache = cloudfront.BehaviorOptions(
            origin=origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=fwd_qs,
        )
        # Cached Lambda GETs (single-night /night, /healthz) keyed on the query string.
        api_cached = cloudfront.BehaviorOptions(
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

        dist = cloudfront.Distribution(
            self, "Cdn",
            comment="PyNightSky SPA + API (S3 default, Lambda for API paths)",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=spa_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            additional_behaviors={
                "/night": api_cached,
                "/healthz": api_cached,
                "/calendar": no_cache,
                "/trip": no_cache,
                "/jobs/*": no_cache,
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

        CfnOutput(self, "CloudFrontUrl", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "LambdaFunctionName", value=fn.function_name)
        CfnOutput(self, "SpaBucketName", value=spa_bucket.bucket_name)
