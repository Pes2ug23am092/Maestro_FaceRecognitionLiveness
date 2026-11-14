# Lambda container image for B's liveness code. Build context = this folder (models/).
#   docker build -t hid-liveness:dev .
#   docker run --rm -p 9000:8080 hid-liveness:dev        # local test with the Lambda Runtime Interface Emulator
#   curl -XPOST http://localhost:9000/2015-03-31/functions/function/invocations -d @event.json
# Push: aws ecr create-repository ...; docker tag/push; create the function from the image (A owns IaC).
FROM public.ecr.aws/lambda/python:3.12

COPY lambda/requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt

COPY liveness/ ${LAMBDA_TASK_ROOT}/liveness/
COPY lambda/handler.py ${LAMBDA_TASK_ROOT}/handler.py
# only the models the Lambda needs (identity is Rekognition on AWS, so SFace is NOT shipped)
COPY artifacts/liveness_cnn.onnx artifacts/face_detection_yunet_2023mar.onnx ${LAMBDA_TASK_ROOT}/artifacts/

CMD ["handler.handler"]
