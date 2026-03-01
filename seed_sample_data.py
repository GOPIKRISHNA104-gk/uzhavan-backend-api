"""
Sample Data Seeder for Mandi Prices
Seeds the database with realistic sample data for development/testing
when the data.gov.in API is unavailable

This data is based on realistic price ranges for fruits and vegetables in India
"""

import asyncio
from datetime import datetime, timedelta
import random
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Import from parent
import sys
sys.path.insert(0, '.')

from config import settings
from database import Base, MandiPrice, PriceFetchLog

# Create separate database engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Sample data configuration
SAMPLE_COMMODITIES = {
    # Commodity: (base_min, base_max, base_modal, unit="Rs/Quintal")
    "Tomato": (1200, 3500, 2200),
    "Onion": (800, 2500, 1500),
    "Potato": (600, 1500, 1000),
    "Brinjal": (1000, 2500, 1800),
    "Cabbage": (500, 1500, 800),
    "Cauliflower": (800, 2000, 1200),
    "Carrot": (1000, 2500, 1500),
    "Beans": (2000, 4000, 2800),
    "Lady Finger": (1500, 3500, 2500),
    "Green Chilli": (2000, 5000, 3000),
    "Capsicum": (2500, 5000, 3500),
    "Cucumber": (800, 2000, 1200),
    "Bitter Gourd": (1500, 3000, 2200),
    "Bottle Gourd": (600, 1500, 900),
    "Pumpkin": (500, 1200, 800),
    "Apple": (5000, 12000, 8000),
    "Banana": (1500, 3500, 2500),
    "Orange": (2000, 5000, 3500),
    "Grapes": (4000, 10000, 6000),
    "Mango": (3000, 8000, 5000),
    "Papaya": (1500, 3500, 2500),
    "Pomegranate": (6000, 15000, 10000),
    "Watermelon": (800, 2000, 1200),
    "Guava": (2000, 4000, 2800),
    "Lemon": (3000, 8000, 5000),
}

SAMPLE_LOCATIONS = [
    # (State, District, Market)
    ("Tamil Nadu", "Chennai", "Koyambedu"),
    ("Tamil Nadu", "Chennai", "Thiruvanmiyur"),
    ("Tamil Nadu", "Coimbatore", "Ukkadam"),
    ("Tamil Nadu", "Madurai", "Gandhi Market"),
    ("Tamil Nadu", "Salem", "Salem"),
    ("Tamil Nadu", "Tiruchirappalli", "Trichy"),
    ("Karnataka", "Bangalore Urban", "K.R. Market"),
    ("Karnataka", "Bangalore Urban", "Yeshwanthpur"),
    ("Karnataka", "Mysore", "Devaraja Market"),
    ("Karnataka", "Hubli", "Hubli"),
    ("Kerala", "Ernakulam", "Kochi"),
    ("Kerala", "Thiruvananthapuram", "Chalai"),
    ("Andhra Pradesh", "Krishna", "Vijayawada"),
    ("Andhra Pradesh", "Visakhapatnam", "Visakhapatnam"),
    ("Telangana", "Hyderabad", "Bowenpally"),
    ("Telangana", "Hyderabad", "Mehdipatnam"),
    ("Maharashtra", "Mumbai", "Vashi"),
    ("Maharashtra", "Pune", "Market Yard"),
    ("Maharashtra", "Nashik", "Nashik"),
    ("Gujarat", "Ahmedabad", "Jamalpur"),
    ("Gujarat", "Surat", "Surat"),
    ("Rajasthan", "Jaipur", "Muhana"),
    ("Delhi", "New Delhi", "Azadpur"),
    ("Delhi", "New Delhi", "Okhla"),
    ("Uttar Pradesh", "Lucknow", "Lucknow"),
    ("Uttar Pradesh", "Varanasi", "Varanasi"),
    ("West Bengal", "Kolkata", "Mechua Market"),
    ("Punjab", "Ludhiana", "Ludhiana"),
    ("Haryana", "Gurugram", "Gurugram"),
    ("Madhya Pradesh", "Indore", "Indore"),
]


def generate_price_variation(base_min: int, base_max: int, base_modal: int, variation: float = 0.15):
    """Generate random price variation around base prices"""
    factor = random.uniform(1 - variation, 1 + variation)
    min_price = int(base_min * factor)
    max_price = int(base_max * factor)
    modal_price = int(base_modal * factor)
    
    # Ensure logical price ordering
    min_price = min(min_price, modal_price)
    max_price = max(max_price, modal_price)
    
    return min_price, max_price, modal_price


async def seed_sample_data(days: int = 30):
    """Seed sample mandi price data for testing"""
    print("🌾 Starting sample data seeding...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as db:
        try:
            records_created = 0
            today = datetime.now()
            
            # Generate data for the last N days
            for day_offset in range(days):
                current_date = today - timedelta(days=day_offset)
                
                # Not all commodities are available every day in every market
                for commodity, (base_min, base_max, base_modal) in SAMPLE_COMMODITIES.items():
                    # Random subset of locations for each commodity
                    selected_locations = random.sample(
                        SAMPLE_LOCATIONS, 
                        k=random.randint(5, 15)
                    )
                    
                    for state, district, market in selected_locations:
                        min_price, max_price, modal_price = generate_price_variation(
                            base_min, base_max, base_modal,
                            variation=0.20  # 20% variation
                        )
                        
                        # Add some seasonal trend
                        if day_offset < 7:  # Recent week - slight price increase
                            trend_factor = 1.05
                        elif day_offset < 14:
                            trend_factor = 1.02
                        else:
                            trend_factor = 1.0
                            
                        min_price = int(min_price * trend_factor)
                        max_price = int(max_price * trend_factor)
                        modal_price = int(modal_price * trend_factor)
                        
                        price_record = MandiPrice(
                            arrival_date=current_date,
                            state=state,
                            district=district,
                            market=market,
                            commodity=commodity,
                            variety="Local",
                            grade="FAQ",
                            min_price=min_price,
                            max_price=max_price,
                            modal_price=modal_price,
                            commodity_code=""
                        )
                        
                        db.add(price_record)
                        records_created += 1
                
                # Progress update
                if day_offset % 5 == 0:
                    print(f"  📅 Generated data for day -{day_offset}")
            
            # Log the seeding operation
            fetch_log = PriceFetchLog(
                fetch_date=datetime.utcnow(),
                records_fetched=records_created,
                records_inserted=records_created,
                status="success",
                duration_seconds=0,
                error_message="Sample data seeded"
            )
            db.add(fetch_log)
            
            await db.commit()
            
            print(f"\n✅ Sample data seeding complete!")
            print(f"📊 Total records created: {records_created}")
            print(f"📅 Days of data: {days}")
            print(f"🥬 Commodities: {len(SAMPLE_COMMODITIES)}")
            print(f"📍 Locations: {len(SAMPLE_LOCATIONS)}")
            
            return {
                "success": True,
                "records_created": records_created,
                "days": days,
                "commodities": len(SAMPLE_COMMODITIES),
                "locations": len(SAMPLE_LOCATIONS)
            }
            
        except Exception as e:
            await db.rollback()
            print(f"❌ Error seeding data: {e}")
            return {
                "success": False,
                "error": str(e)
            }


async def clear_sample_data():
    """Clear all sample data from the database"""
    print("🗑️ Clearing sample data...")
    
    async with async_session() as db:
        try:
            # Delete all mandi prices
            from sqlalchemy import delete
            await db.execute(delete(MandiPrice))
            await db.execute(delete(PriceFetchLog))
            await db.commit()
            
            print("✅ Sample data cleared!")
            return {"success": True}
            
        except Exception as e:
            await db.rollback()
            print(f"❌ Error clearing data: {e}")
            return {"success": False, "error": str(e)}


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Seed sample mandi price data")
    parser.add_argument("--days", type=int, default=30, help="Number of days of data to generate")
    parser.add_argument("--clear", action="store_true", help="Clear existing data before seeding")
    
    args = parser.parse_args()
    
    if args.clear:
        await clear_sample_data()
    
    await seed_sample_data(days=args.days)


if __name__ == "__main__":
    asyncio.run(main())
