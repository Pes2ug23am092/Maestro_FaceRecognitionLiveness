from mangum import Mangum
from backend.main import app

# Mangum adapts ASGI FastAPI app for AWS Lambda with API Gateway HTTP / REST proxy
handler = Mangum(app, lifespan="off")
