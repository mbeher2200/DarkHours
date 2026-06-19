import { AwsRum, type AwsRumConfig } from "aws-rum-web";

try {
  const config: AwsRumConfig = {
    sessionSampleRate: 1,
    endpoint: "https://dataplane.rum.us-east-1.amazonaws.com",
    telemetries: ["performance", "errors", "http"],
    allowCookies: false,
    enableXRay: false,
    signing: false, // Public resource policy is configured, so send unsigned requests
  };

  const APPLICATION_ID: string = "93fd6beb-0fed-4d28-9632-0a7406d189b4";
  const APPLICATION_VERSION: string = "1.0.0";
  const APPLICATION_REGION: string = "us-east-1";

  new AwsRum(APPLICATION_ID, APPLICATION_VERSION, APPLICATION_REGION, config);
} catch (error) {
  // Ignore errors thrown during CloudWatch RUM web client initialization
}
