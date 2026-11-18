#!/usr/bin/env bash
# Demo helper.  Usage:
#   ./scripts/demo.sh upload photo.jpg   # the happy path
#   ./scripts/demo.sh fail               # upload a corrupt .jpg (shows retries + DLQ)
#   ./scripts/demo.sh show               # list saved photos and tags
#   ./scripts/demo.sh logs               # tail the Lambda logs
set -euo pipefail
STACK="${STACK_NAME:-photo-butler}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

case "${1:-}" in
  upload)
    aws s3 cp "${2:?usage: demo.sh upload <file.jpg>}" "s3://$(out UploadBucketName)/"
    ;;
  fail)
    echo "this is not an image" > /tmp/corrupt.jpg
    aws s3 cp /tmp/corrupt.jpg "s3://$(out UploadBucketName)/corrupt.jpg"
    echo "Lambda will retry twice, then the event lands in the failed-photos queue."
    echo "Check: aws sqs receive-message --queue-url $(out FailedQueueUrl)"
    ;;
  show)
    aws dynamodb scan --table-name "$(out TableName)" \
      --query 'Items[].{photo:photoId.S,top:topLabel.S,uploaded:uploadedAt.S}' --output table
    ;;
  logs)
    aws logs tail "/aws/lambda/$(out FunctionName)" --follow
    ;;
  *)
    echo "usage: $0 {upload <file>|fail|show|logs}"; exit 1
    ;;
esac
