from mangum import Mangum
from apps.api.main import app

handler = Mangum(app, lifespan="on")
