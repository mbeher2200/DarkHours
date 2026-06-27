"""CI/CD trust + deploy role for keyless GitHub Actions deploys (M5).

GitHub Actions authenticates to AWS with **no stored keys** via OpenID Connect:
each workflow job is handed a short-lived OIDC token (a signed JWT describing the
repo/branch/job). AWS trusts those tokens through an IAM OIDC *identity provider*,
and a *deploy role* whose trust policy only lets tokens from THIS repo's `main`
branch assume it. The job calls AssumeRoleWithWebIdentity → ~1h temporary creds.

Least privilege: the role can't do much directly. It can push our app image to the
one ECR repo, and it can *assume the CDK bootstrap roles* — which are what actually
perform the CloudFormation deploy. So a leaked workflow can only deploy this app's
stacks, nothing else.

Nothing here hardcodes the account id (uses ``self.account``). The GitHub repo slug
is the repo's own public identity, so it's fine in source; override via ``GITHUB_REPO``.
"""
import os

from aws_cdk import (
    Stack,
    CfnOutput,
    Tags,
    aws_iam as iam,
)
from constructs import Construct

# Subject claim format GitHub puts in the OIDC token: "repo:<owner>/<name>:<ref>".
_GITHUB_REPO = os.environ.get("GITHUB_REPO", "mbeher2200/PyNightSkyPredictor")
# Only the main branch may assume the deploy role (matches the push-to-main trigger).
_ALLOWED_SUB = f"repo:{_GITHUB_REPO}:ref:refs/heads/main"
_GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
# Default CDK bootstrap qualifier (the `cdk-hnb659fds-*` roles created by `cdk bootstrap`).
_BOOTSTRAP_QUALIFIER = "hnb659fds"
_ECR_REPO_NAMES = ["pynightsky-worker"]   # api is a zip Lambda; no ECR push needed


class CicdStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Component", "cicd")

        # --- the GitHub OIDC identity provider (one per account for this URL) ---
        # If the account already has this provider, import it instead of creating a
        # duplicate: `iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(...)`.
        provider = iam.OpenIdConnectProvider(
            self, "GitHubOidc",
            url=_GITHUB_OIDC_URL,
            client_ids=["sts.amazonaws.com"],   # the audience GitHub's action requests
        )

        # --- the deploy role GitHub Actions assumes ---
        # Trust = WebIdentity from our provider, gated on BOTH:
        #   aud == sts.amazonaws.com  AND  sub == repo:<owner>/<name>:ref:refs/heads/main
        deploy_role = iam.Role(
            self, "GitHubDeployRole",
            role_name="pynightsky-github-deploy",
            description="Assumed by GitHub Actions (main branch) to deploy PyNightSkyLambda.",
            max_session_duration=None,          # default 1h is plenty for a deploy
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": _ALLOWED_SUB,
                    },
                },
            ),
        )

        # (1) Assume the CDK bootstrap roles — these do the actual CloudFormation work
        #     (deploy / publish file+image assets / context lookups). This is the
        #     recommended least-privilege pattern: the OIDC role itself holds no broad
        #     CFN/IAM powers; it just borrows the scoped bootstrap roles at deploy time.
        deploy_role.add_to_policy(iam.PolicyStatement(
            sid="AssumeCdkBootstrapRoles",
            actions=["sts:AssumeRole"],
            resources=[
                f"arn:aws:iam::{self.account}:role/cdk-{_BOOTSTRAP_QUALIFIER}-*-{self.account}-{self.region}",
            ],
        ))

        # (2) Push to the worker ECR repo (used only by the benchmark script; not CI).
        #     GetAuthorizationToken is account-wide (can't be resource-scoped);
        #     the layer/image actions are scoped to our one repo.
        deploy_role.add_to_policy(iam.PolicyStatement(
            sid="EcrAuth",
            actions=["ecr:GetAuthorizationToken"],
            resources=["*"],
        ))
        deploy_role.add_to_policy(iam.PolicyStatement(
            sid="EcrPushPull",
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
            ],
            resources=[
                f"arn:aws:ecr:{self.region}:{self.account}:repository/{name}"
                for name in _ECR_REPO_NAMES
            ],
        ))

        CfnOutput(self, "DeployRoleArn", value=deploy_role.role_arn,
                  description="Set as the GitHub secret AWS_DEPLOY_ROLE_ARN")
        CfnOutput(self, "OidcProviderArn", value=provider.open_id_connect_provider_arn)
