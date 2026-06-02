"""App Runner service + least-privilege IAM for the PyNightSky API.

Foundational resources (the S3 raster bucket and DynamoDB cache table) are
created outside IaC and only *referenced* here — never managed/deleted by this
stack. Bucket/table names come from the environment so the public repo carries
no identifiers.
"""
import os

from aws_cdk import (
    Stack,
    CfnOutput,
    Tags,
    aws_apprunner as apprunner,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class PyNightSkyStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        # --- config from env (kept out of source) ---
        raster_bucket = os.environ["PYNIGHTSKY_RASTER_BUCKET"]
        cache_table = os.environ["PYNIGHTSKY_CACHE_TABLE"]
        image_uri = f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/pynightsky-api:latest"

        # --- cost-allocation tags (applied to everything in the stack) ---
        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Env", "prod")
        Tags.of(self).add("Component", "api")

        # --- reference foundational resources (NOT managed here) ---
        bucket = s3.Bucket.from_bucket_name(self, "RasterBucket", raster_bucket)
        table = dynamodb.Table.from_table_name(self, "CacheTable", cache_table)

        # --- instance role: least-privilege access for the running container ---
        instance_role = iam.Role(
            self, "InstanceRole",
            assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
            description="PyNightSky API runtime: read rasters from S3, read/write the cache table",
        )
        bucket.grant_read(instance_role)             # s3:GetObject on the raster bucket
        table.grant_read_write_data(instance_role)   # DynamoDB cache get/set/invalidate

        # --- access role: lets App Runner pull the image from private ECR ---
        access_role = iam.Role(
            self, "EcrAccessRole",
            assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSAppRunnerServicePolicyForECRAccess"),
            ],
        )

        # --- the App Runner service (L1 CfnService — stable, no alpha module) ---
        service = apprunner.CfnService(
            self, "Service",
            service_name="pynightsky-api",
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                auto_deployments_enabled=True,  # redeploy when a new image is pushed
                authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                    access_role_arn=access_role.role_arn,
                ),
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=image_uri,
                    image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port="8080",
                        runtime_environment_variables=[
                            apprunner.CfnService.KeyValuePairProperty(
                                name="PYNIGHTSKY_BACKEND", value="aws"),
                            apprunner.CfnService.KeyValuePairProperty(
                                name="PYNIGHTSKY_CACHE_TABLE", value=cache_table),
                            apprunner.CfnService.KeyValuePairProperty(
                                name="PYNIGHTSKY_RASTER_BUCKET", value=raster_bucket),
                            apprunner.CfnService.KeyValuePairProperty(
                                name="AWS_REGION", value=self.region),
                        ],
                    ),
                ),
            ),
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                cpu="1024",       # 1 vCPU
                memory="2048",    # 2 GB
                instance_role_arn=instance_role.role_arn,
            ),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP",
                path="/healthz",
                interval=10,
                timeout=5,
                healthy_threshold=1,
                unhealthy_threshold=5,
            ),
        )

        CfnOutput(self, "ServiceUrl", value=f"https://{service.attr_service_url}")
        CfnOutput(self, "ServiceArn", value=service.attr_service_arn)
