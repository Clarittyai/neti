#!/usr/bin/env bash
#
# Create or update `neti-contact` and print its Function URL.
#
# Idempotent on purpose: every step checks for the thing before making it, so this is safe to run
# again after a change to `index.mjs` and safe to run twice by accident. Nothing here destroys
# anything — there is no `delete` in this file, and removing the function is a deliberate manual act
# rather than something a re-run can do by surprise.
#
# Usage:
#   CONTACT_SECRET=<a long random string> ./deploy.sh
#
# The secret is the only thing the Vercel side needs, and it is never written to the repository.
# Generate one with `openssl rand -hex 32`.
set -euo pipefail

FUNCTION=neti-contact
ROLE=neti-contact-lambda
REGION="${AWS_REGION:-us-east-1}"
CONTACT_TO="${CONTACT_TO:-shahar@claritty.ai}"
CONTACT_FROM="${CONTACT_FROM:-neti <noreply@mail.claritty.ai>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${CONTACT_SECRET:-}" ]]; then
  echo "CONTACT_SECRET is not set. Generate one with: openssl rand -hex 32" >&2
  exit 2
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/5  execution role"
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "  $ROLE exists"
else
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document "file://$here/trust.json" \
    --description "Sends the neti.claritty.ai contact form via SES. Nothing else." >/dev/null
  echo "  created $ROLE"
fi
# Both are idempotent: put-role-policy overwrites, attach is a no-op when already attached.
aws iam put-role-policy --role-name "$ROLE" --policy-name ses-send \
  --policy-document "file://$here/ses.json"
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"
echo "  $ROLE_ARN"

say "2/5  package"
BUILD="$(mktemp -d)"
cp "$here/index.mjs" "$BUILD/"
# `@aws-sdk/client-sesv2` is present in the Lambda Node 22 runtime image, so the zip stays a single
# file. Vendoring it would add ~3MB to every deploy for a module that is already there.
(cd "$BUILD" && zip -q -r function.zip index.mjs)
echo "  $(du -h "$BUILD/function.zip" | cut -f1) zip"

say "3/5  function"
ENV_VARS="Variables={CONTACT_TO=$CONTACT_TO,CONTACT_SECRET=$CONTACT_SECRET,CONTACT_FROM=$CONTACT_FROM}"
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" --region "$REGION" \
    --zip-file "fileb://$BUILD/function.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 15 --memory-size 256 >/dev/null
  echo "  updated $FUNCTION"
else
  # The role is eventually consistent — a brand-new role is not always assumable the instant
  # create-role returns, and the failure looks like a permissions bug rather than a timing one.
  for attempt in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
      --runtime nodejs22.x --handler index.handler --role "$ROLE_ARN" \
      --zip-file "fileb://$BUILD/function.zip" --timeout 15 --memory-size 256 \
      --environment "$ENV_VARS" \
      --description "The contact form on neti.claritty.ai/cloud, delivered by SES." >/dev/null 2>&1
    then
      echo "  created $FUNCTION"
      break
    fi
    echo "  waiting for the role to propagate ($attempt/6)"
    sleep 5
  done
fi
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"

say "4/5  function URL"
if ! aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1
then
  # AuthType NONE, guarded by the shared secret the function checks in constant time. IAM auth
  # would mean giving Vercel an AWS key to sign with, which is the thing this design avoids.
  aws lambda create-function-url-config --function-name "$FUNCTION" --region "$REGION" \
    --auth-type NONE >/dev/null
  aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
    --statement-id public-function-url --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
  echo "  created"
else
  echo "  exists"
fi
URL="$(aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" \
  --query FunctionUrl --output text)"

say "5/5  done"
cat <<EOF
  Set these in the Vercel project (Settings -> Environment Variables):

    CONTACT_LAMBDA_URL     $URL
    CONTACT_LAMBDA_SECRET  <the CONTACT_SECRET you passed to this script>

  The recipient lives on the Lambda, not on Vercel, so nothing on the web side can redirect it.
EOF
rm -rf "$BUILD"
