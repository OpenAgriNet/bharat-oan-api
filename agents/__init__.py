from dotenv import load_dotenv
load_dotenv()
from app.services.telemetry import telemetry_init
# Initialise Langfuse and instrument agents at module load
telemetry_init()
