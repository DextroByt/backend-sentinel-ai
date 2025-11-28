import asyncio
import random
from app.services.verification_orchestrator import run_verification_pipeline

# Test Claims to simulate "Viral News"
TEST_CLAIMS = [
    "Major floods reported in downtown area",
    "Aliens landed in the city center",
    "Government declares holiday tomorrow",
]

async def start_monitoring():
    """
    Background Task.
    Ref: Blueprint Section 4.1 (Autonomous Scanning)
    """
    print("Scanner Service: STARTED (Monitoring News...)")
    
    while True:
        try:
            # SIMULATION: Pick a random claim to verify
            # In real life, this calls NewsAPI
            claim = random.choice(TEST_CLAIMS)
            print(f"\nScanner: Detected viral claim -> {claim}")
            
            # Send to Orchestrator
            await run_verification_pipeline(claim)
            
            # Sleep for demo purposes (e.g., 30 seconds)
            await asyncio.sleep(30)
            
        except asyncio.CancelledError:
            print("Scanner: Stopping...")
            break
        except Exception as e:
            print(f"Scanner Error: {e}")
            await asyncio.sleep(10)