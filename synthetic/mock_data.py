"""
Data pools for synthetic conversation generation.
Indian names, locations, mandi names, crop price ranges, weather ranges, soil health ranges.
"""

import random
from typing import Tuple

# ─── Failure Simulation ──────────────────────────────────────────────────────────

MOCK_FAILURE_RATE = 0.05  # 5% chance of simulated service failure


def should_fail() -> bool:
    """Return True if this call should simulate a failure."""
    return random.random() < MOCK_FAILURE_RATE


# ─── Indian Names ───────────────────────────────────────────────────────────────

MALE_FIRST_NAMES = [
    "Ramesh", "Suresh", "Mahesh", "Rajesh", "Dinesh", "Mukesh", "Rakesh",
    "Anil", "Sunil", "Vijay", "Sanjay", "Ajay", "Ravi", "Mohan", "Sohan",
    "Gopal", "Kishan", "Bhagwan", "Shankar", "Manoj", "Pramod", "Vinod",
    "Ashok", "Deepak", "Prakash", "Ganesh", "Balram", "Hari", "Shyam",
    "Ram", "Lakshman", "Bharat", "Arjun", "Karan", "Devendra", "Narendra",
    "Jagdish", "Satish", "Girish", "Umesh", "Kamlesh", "Yogesh",
]

FEMALE_FIRST_NAMES = [
    "Sunita", "Anita", "Savita", "Kavita", "Mamta", "Rekha", "Sushma",
    "Kamla", "Geeta", "Seema", "Neema", "Radha", "Sita", "Lakshmi",
    "Parvati", "Durga", "Meena", "Pushpa", "Saroj", "Usha",
]

LAST_NAMES = [
    "Kumar", "Sharma", "Verma", "Singh", "Yadav", "Patel", "Reddy",
    "Gupta", "Joshi", "Tiwari", "Mishra", "Pandey", "Dubey", "Chauhan",
    "Rajput", "Thakur", "Patil", "Jadhav", "Deshmukh", "Kulkarni",
    "Naik", "Nair", "Pillai", "Rathore", "Meena", "Gowda", "Swamy",
    "Choudhary", "Mandal", "Das", "Mahto", "Sahu", "Dehri",
]


def random_name() -> str:
    first = random.choice(MALE_FIRST_NAMES + FEMALE_FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    return f"{first} {last}"


# ─── Indian Locations (state, district, village, lat, lon) ──────────────────────

LOCATIONS: list[dict] = [
    {"state": "Madhya Pradesh", "district": "Indore", "village": "Mhow", "lat": 22.55, "lon": 75.76},
    {"state": "Madhya Pradesh", "district": "Bhopal", "village": "Berasia", "lat": 23.63, "lon": 77.43},
    {"state": "Madhya Pradesh", "district": "Sagar", "village": "Khurai", "lat": 24.04, "lon": 78.33},
    {"state": "Madhya Pradesh", "district": "Ujjain", "village": "Nagda", "lat": 23.45, "lon": 75.42},
    {"state": "Uttar Pradesh", "district": "Lucknow", "village": "Malihabad", "lat": 26.92, "lon": 80.71},
    {"state": "Uttar Pradesh", "district": "Varanasi", "village": "Ramnagar", "lat": 25.27, "lon": 83.03},
    {"state": "Uttar Pradesh", "district": "Agra", "village": "Fatehabad", "lat": 27.10, "lon": 78.02},
    {"state": "Uttar Pradesh", "district": "Kanpur", "village": "Bilhaur", "lat": 26.87, "lon": 80.06},
    {"state": "Rajasthan", "district": "Jaipur", "village": "Chomu", "lat": 27.17, "lon": 75.72},
    {"state": "Rajasthan", "district": "Jodhpur", "village": "Osian", "lat": 26.73, "lon": 72.91},
    {"state": "Rajasthan", "district": "Kota", "village": "Sangod", "lat": 24.93, "lon": 76.28},
    {"state": "Maharashtra", "district": "Pune", "village": "Junnar", "lat": 19.21, "lon": 73.88},
    {"state": "Maharashtra", "district": "Nagpur", "village": "Kamptee", "lat": 21.22, "lon": 79.20},
    {"state": "Maharashtra", "district": "Nashik", "village": "Sinnar", "lat": 19.85, "lon": 73.99},
    {"state": "Maharashtra", "district": "Latur", "village": "Udgir", "lat": 18.39, "lon": 77.12},
    {"state": "Gujarat", "district": "Ahmedabad", "village": "Dholka", "lat": 22.72, "lon": 72.44},
    {"state": "Gujarat", "district": "Rajkot", "village": "Gondal", "lat": 21.96, "lon": 70.80},
    {"state": "Gujarat", "district": "Surat", "village": "Bardoli", "lat": 21.12, "lon": 73.11},
    {"state": "Punjab", "district": "Ludhiana", "village": "Jagraon", "lat": 30.79, "lon": 75.47},
    {"state": "Punjab", "district": "Amritsar", "village": "Ajnala", "lat": 31.84, "lon": 74.76},
    {"state": "Punjab", "district": "Patiala", "village": "Rajpura", "lat": 30.48, "lon": 76.59},
    {"state": "Haryana", "district": "Karnal", "village": "Gharaunda", "lat": 29.54, "lon": 76.97},
    {"state": "Haryana", "district": "Hisar", "village": "Hansi", "lat": 29.10, "lon": 75.97},
    {"state": "Bihar", "district": "Patna", "village": "Danapur", "lat": 25.64, "lon": 85.05},
    {"state": "Bihar", "district": "Muzaffarpur", "village": "Sahebganj", "lat": 26.12, "lon": 85.39},
    {"state": "Karnataka", "district": "Bengaluru Rural", "village": "Devanahalli", "lat": 13.25, "lon": 77.71},
    {"state": "Karnataka", "district": "Mysuru", "village": "Nanjangud", "lat": 12.12, "lon": 76.68},
    {"state": "Karnataka", "district": "Belgaum", "village": "Gokak", "lat": 16.17, "lon": 74.82},
    {"state": "Tamil Nadu", "district": "Coimbatore", "village": "Pollachi", "lat": 10.66, "lon": 77.01},
    {"state": "Tamil Nadu", "district": "Thanjavur", "village": "Kumbakonam", "lat": 10.96, "lon": 79.39},
    {"state": "Andhra Pradesh", "district": "Guntur", "village": "Tenali", "lat": 16.24, "lon": 80.64},
    {"state": "Andhra Pradesh", "district": "Krishna", "village": "Machilipatnam", "lat": 16.19, "lon": 81.14},
    {"state": "Telangana", "district": "Warangal", "village": "Jangaon", "lat": 17.73, "lon": 79.15},
    {"state": "Telangana", "district": "Karimnagar", "village": "Huzurabad", "lat": 18.40, "lon": 79.40},
    {"state": "West Bengal", "district": "Burdwan", "village": "Memari", "lat": 23.20, "lon": 88.11},
    {"state": "Odisha", "district": "Cuttack", "village": "Banki", "lat": 20.38, "lon": 85.53},
    {"state": "Chhattisgarh", "district": "Raipur", "village": "Abhanpur", "lat": 21.15, "lon": 81.73},
    {"state": "Jharkhand", "district": "Ranchi", "village": "Kanke", "lat": 23.42, "lon": 85.32},
    {"state": "Assam", "district": "Kamrup", "village": "Mirza", "lat": 26.10, "lon": 91.57},
    {"state": "Kerala", "district": "Thrissur", "village": "Chalakudy", "lat": 10.31, "lon": 76.33},
]


# ─── Mandi Names ───────────────────────────────────────────────────────────────

MANDI_NAMES = [
    "Indore Mandi", "Bhopal Mandi", "Ujjain Mandi", "Dewas Mandi",
    "Lucknow Mandi", "Kanpur Mandi", "Agra Mandi", "Varanasi Mandi",
    "Jaipur Mandi", "Jodhpur Mandi", "Kota Mandi", "Ajmer Mandi",
    "Pune Mandi", "Nagpur Mandi", "Nashik Mandi", "Aurangabad Mandi",
    "Ahmedabad Mandi", "Rajkot Mandi", "Surat Mandi", "Vadodara Mandi",
    "Ludhiana Mandi", "Amritsar Mandi", "Patiala Mandi", "Bathinda Mandi",
    "Karnal Mandi", "Hisar Mandi", "Sirsa Mandi", "Rohtak Mandi",
    "Patna Mandi", "Muzaffarpur Mandi", "Darbhanga Mandi",
    "Guntur Mandi", "Warangal Mandi", "Coimbatore Mandi",
]


# ─── Crop Price Ranges (INR per quintal) ───────────────────────────────────────

CROP_PRICE_RANGES: dict[str, Tuple[int, int]] = {
    "Wheat": (2000, 2800),
    "Paddy(Common)": (1800, 2600),
    "Rice": (2800, 4200),
    "Maize": (1600, 2400),
    "Jowar(Sorghum)": (2200, 3500),
    "Bengal Gram(Gram)(Whole)": (4500, 6500),
    "Red Gram": (5500, 8000),
    "Black Gram(Urd Beans)(Whole)": (5000, 7500),
    "Green Gram(Moong)(Whole)": (6000, 8500),
    "Groundnut": (4500, 6500),
    "Sesamum(Sesame,Gingelly,Til)": (8000, 14000),
    "Mustard": (4500, 6500),
    "Soyabean": (3800, 5500),
    "Sunflower": (4500, 6500),
    "Cotton": (5500, 8000),
    "Jute": (3500, 5500),
    "Apple": (3000, 8000),
    "Orange": (2000, 5000),
    "Banana": (1000, 3000),
    "Mango": (2000, 6000),
    "Onion": (800, 3500),
    "Potato": (600, 2000),
    "Garlic": (3000, 10000),
    "Chili Red": (8000, 18000),
    "Ginger(Dry)": (5000, 12000),
    "Bajra(Pearl Millet/Cumbu)": (1800, 2800),
    "Barley(Jau)": (1500, 2200),
    "Ragi(Finger Millet)": (2800, 4000),
    "Sugarcane": (280, 380),
    "Turmeric": (6000, 12000),
}

# Default price range for commodities not in the dict
DEFAULT_PRICE_RANGE = (2000, 5000)

# Commodity-specific varieties (key matches CROP_PRICE_RANGES names)
COMMODITY_VARIETIES: dict[str, list[str]] = {
    "Wheat": ["Sharbati", "Lokwan", "MP Wheat", "Dara", "Raj 3765", "PBW 343", "Local"],
    "Paddy(Common)": ["Swarna", "IR-64", "Sona Masuri", "BPT 5204", "HMT", "Local"],
    "Rice": ["Basmati", "Sona Masuri", "Ponni", "HMT", "IR-64", "Kolam"],
    "Maize": ["Yellow", "White", "Hybrid", "Desi", "Pop Corn", "Local"],
    "Jowar(Sorghum)": ["Maldandi", "Hybrid", "Yellow", "White", "Local"],
    "Bengal Gram(Gram)(Whole)": ["Desi", "Kabuli", "Bold", "Medium", "Local"],
    "Red Gram": ["Tur Dal", "Bold", "Medium", "Local"],
    "Black Gram(Urd Beans)(Whole)": ["Bold", "Medium", "Desi", "Local"],
    "Green Gram(Moong)(Whole)": ["Bold", "Medium", "Desi", "Local"],
    "Groundnut": ["Bold", "Java", "TJ", "G-20", "Local"],
    "Sesamum(Sesame,Gingelly,Til)": ["White", "Black", "Brown", "Local"],
    "Mustard": ["Laha", "Sarson", "Rai", "Bold", "Local"],
    "Soyabean": ["Yellow", "JS 335", "JS 9560", "Local"],
    "Sunflower": ["Hybrid", "Morden", "Local"],
    "Cotton": ["H-4", "Shankar-6", "MCU-5", "DCH-32", "Medium Staple", "Long Staple"],
    "Jute": ["TD-5", "JRO 524", "White", "Tossa", "Local"],
    "Onion": ["Nasik Red", "Bellary", "Poona", "White", "Local"],
    "Potato": ["Jyoti", "Kufri", "Chandramukhi", "Pukhraj", "Local"],
    "Garlic": ["Desi", "Hybrid", "Single Clove", "Local"],
    "Chili Red": ["Guntur", "Byadgi", "Teja", "Kashmiri", "Local"],
    "Turmeric": ["Erode", "Salem", "Nizamabad", "Rajapuri", "Local"],
    "Apple": ["Royal Delicious", "Golden", "Red Delicious", "Shimla", "Kashmiri"],
    "Orange": ["Nagpur", "Kinnow", "Mosambi", "Local"],
    "Banana": ["Robusta", "Poovan", "Nendran", "Elakki", "Local"],
    "Mango": ["Alphonso", "Dasheri", "Langra", "Totapuri", "Kesar", "Local"],
    "Sugarcane": ["Co 86032", "CoC 671", "Co 0238", "Local"],
    "Bajra(Pearl Millet/Cumbu)": ["Hybrid", "Desi", "Bold", "Local"],
    "Barley(Jau)": ["Feed", "Malt", "Desi", "Local"],
    "Ragi(Finger Millet)": ["Local", "Hybrid", "Bold"],
    "Ginger(Dry)": ["Maran", "Nadia", "Ernad", "Local"],
}
DEFAULT_VARIETIES = ["Local", "Desi", "Hybrid", "FAQ"]

GRADES = ["FAQ", "Non-FAQ", "Average", "Superior", "Ordinary"]


# ─── Weather Ranges ─────────────────────────────────────────────────────────────

WEATHER_CONDITIONS = [
    "Clear Sky", "Partly Cloudy", "Mostly Cloudy", "Overcast",
    "Light Rain", "Moderate Rain", "Heavy Rain", "Thunderstorm",
    "Haze", "Fog", "Mist", "Sunny",
]

WIND_DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def random_temp(season: str = "summer") -> Tuple[float, float]:
    """Return (min_temp, max_temp) based on season."""
    ranges = {
        "summer": (28, 45),
        "winter": (5, 25),
        "monsoon": (22, 36),
        "spring": (18, 35),
    }
    lo, hi = ranges.get(season, (20, 38))
    min_t = round(random.uniform(lo, lo + (hi - lo) * 0.4), 1)
    max_t = round(random.uniform(min_t + 3, hi), 1)
    return min_t, max_t


def random_humidity() -> int:
    return random.randint(30, 95)


def random_rainfall() -> float:
    # 60% chance of no rain
    if random.random() < 0.6:
        return 0.0
    return round(random.uniform(0.5, 100.0), 1)


def random_wind_speed() -> float:
    return round(random.uniform(5, 40), 1)


# ─── Soil Health Card Data ──────────────────────────────────────────────────────

SHC_SOIL_TYPES = [
    "Sandy Soil", "Sandy Loam", "Loamy Soil", "Clay Loam",
    "Clay Soil", "Silty Clay", "Silty Loam", "Red Soil",
    "Black Cotton Soil", "Alluvial Soil", "Laterite Soil",
]

# Crop fertilizer recommendation templates for SHC reports
# Amounts are base values in Kg Per Acre; tool adds ±2 kg randomization
SHC_CROP_FERTILIZERS = [
    {"crop": "Paddy (Local Variety)", "combo1": {"DAP": 17, "Urea": 45}, "combo2": {"SSP": 50, "Urea": 52}},
    {"crop": "Wheat (Desi)", "combo1": {"DAP": 17, "MOP": 10, "Urea": 32}, "combo2": {"SSP": 50, "MOP": 10, "Urea": 39}},
    {"crop": "Maize (Local)", "combo1": {"DAP": 17, "MOP": 18, "Urea": 45}, "combo2": {"SSP": 50, "MOP": 18, "Urea": 52}},
    {"crop": "Sunflower", "combo1": {"DAP": 26, "Urea": 42}, "combo2": {"SSP": 75, "Urea": 52}},
    {"crop": "Sugarcane (Planted)", "combo1": {"DAP": 22, "MOP": 22, "Urea": 136}, "combo2": {"SSP": 63, "MOP": 22, "Urea": 145}},
    {"crop": "Mustard (Irrigated)", "combo1": {"DAP": 13, "Urea": 47}, "combo2": {"SSP": 38, "Urea": 52}},
    {"crop": "Cotton (Hybrid)", "combo1": {"DAP": 22, "MOP": 15, "Urea": 55}, "combo2": {"SSP": 63, "MOP": 15, "Urea": 62}},
    {"crop": "Soybean", "combo1": {"DAP": 33, "Urea": 10}, "combo2": {"SSP": 95, "Urea": 15}},
    {"crop": "Gram (Desi)", "combo1": {"DAP": 22, "Urea": 10}, "combo2": {"SSP": 63, "Urea": 15}},
    {"crop": "Groundnut", "combo1": {"DAP": 22, "MOP": 10, "Urea": 15}, "combo2": {"SSP": 63, "MOP": 10, "Urea": 20}},
    {"crop": "Bajra (Pearl Millet)", "combo1": {"DAP": 17, "Urea": 35}, "combo2": {"SSP": 50, "Urea": 42}},
    {"crop": "Jowar (Sorghum)", "combo1": {"DAP": 17, "MOP": 8, "Urea": 35}, "combo2": {"SSP": 50, "MOP": 8, "Urea": 42}},
    {"crop": "Potato", "combo1": {"DAP": 33, "MOP": 25, "Urea": 55}, "combo2": {"SSP": 95, "MOP": 25, "Urea": 62}},
    {"crop": "Onion", "combo1": {"DAP": 22, "MOP": 15, "Urea": 45}, "combo2": {"SSP": 63, "MOP": 15, "Urea": 52}},
    {"crop": "Tomato", "combo1": {"DAP": 26, "MOP": 20, "Urea": 50}, "combo2": {"SSP": 75, "MOP": 20, "Urea": 57}},
    {"crop": "Barley", "combo1": {"DAP": 17, "Urea": 28}, "combo2": {"SSP": 50, "Urea": 35}},
    {"crop": "Lentil (Masoor)", "combo1": {"DAP": 22, "Urea": 8}, "combo2": {"SSP": 63, "Urea": 12}},
]

SHC_ORGANIC_REC = (
    "FYM: 12 Tonne Per Hectare\n"
    "Compost / Enriched Compost: 6.0 Tonne Per Hectare\n"
    "Vermicompost: 3.0 Tonne Per Hectare\n"
    "Oil Cake (Neem/Castor/Karanja): 0.80 Tonne Per Hectare\n"
    "Bio-fertilizers: Rhizobium (for legumes) / Azotobacter + PSB @ 2 kg/ha"
)


# ─── PM-KISAN Status Data ──────────────────────────────────────────────────────

INSTALLMENT_AMOUNTS = [2000, 2000, 2000]  # 3 installments per year

PAYMENT_MODES = ["Aadhaar", "Account Number"]

BANK_NAMES = [
    "State Bank of India", "Punjab National Bank", "Bank of Baroda",
    "Canara Bank", "Union Bank of India", "Indian Bank",
    "Central Bank of India", "Bank of India", "UCO Bank",
    "Indian Overseas Bank", "IDBI Bank",
]


# ─── PMFBY Status Data ─────────────────────────────────────────────────────────

SEASONS = ["Kharif", "Rabi", "Summer"]
PMFBY_CROPS = ["Paddy", "Wheat", "Cotton", "Soybean", "Maize", "Groundnut", "Jowar", "Bajra"]
CLAIM_STATUSES = ["Approved", "Under Assessment", "Settled", "Rejected", "Pending"]
POLICY_STATUSES = ["Active", "Expired", "Cancelled", "Under Review"]


# ─── Grievance Data ─────────────────────────────────────────────────────────────

GRIEVANCE_STATUSES = ["Pending", "Under Review", "Resolved", "Forwarded to State"]

OFFICER_REPLIES = [
    "Your grievance has been noted and is being processed. Please wait for resolution.",
    "We are looking into your complaint. Expected resolution within 15 working days.",
    "Your issue has been forwarded to the concerned district officer.",
    "The matter has been resolved. Please check your bank account for updated status.",
    "We need additional documents. Please visit your nearest CSC center.",
    "Your registration details have been corrected. Changes will reflect in the next cycle.",
]


# ─── Scenario Definitions ──────────────────────────────────────────────────────

SCENARIOS: list[dict] = [
    {
        "id": "weather",
        "description_en": "Ask about the weather forecast for your area for the next few days. You want to plan your farming activities.",
        "description_hi": "अपने क्षेत्र के अगले कुछ दिनों के मौसम का हाल पूछें। आप अपनी खेती की गतिविधियों की योजना बनाना चाहते हैं।",
    },
    {
        "id": "mandi_prices",
        "description_en": "Ask about current mandi prices for one of your crops near your location.",
        "description_hi": "अपनी फसल की मंडी भाव के बारे में पूछें।",
    },
    {
        "id": "scheme_info",
        "description_en": "Ask about a government agricultural scheme (PM-KISAN, PMFBY, SHC, KCC, or any other). Ask what it is, who is eligible, and how to apply.",
        "description_hi": "किसी सरकारी कृषि योजना के बारे में पूछें। यह क्या है, कौन पात्र है, और कैसे आवेदन करें।",
    },
    {
        "id": "pmkisan_status",
        "description_en": "Check your PM-KISAN benefit status. Provide your registration number when asked, and use OTP 1234 when prompted.",
        "description_hi": "अपनी पीएम-किसान लाभ स्थिति जांचें। पंजीकरण संख्या दें और OTP 1234 का उपयोग करें।",
    },
    {
        "id": "pmfby_status",
        "description_en": "Check your PMFBY crop insurance status. Provide your phone number when asked, and use OTP 123456 when prompted. Mention the season and year.",
        "description_hi": "अपनी पीएमएफबीवाई फसल बीमा स्थिति जांचें। फोन नंबर दें और OTP 123456 का उपयोग करें।",
    },
    {
        "id": "shc_status",
        "description_en": "Check your Soil Health Card status. Provide your phone number and ask about the latest cycle.",
        "description_hi": "अपनी मिट्टी स्वास्थ्य कार्ड की स्थिति जांचें। अपना फोन नंबर दें।",
    },
    {
        "id": "grievance_submit",
        "description_en": "You have a problem with your PM-KISAN payment — either you haven't received the installment or your bank account details are wrong. Submit a grievance.",
        "description_hi": "आपकी पीएम-किसान भुगतान में समस्या है। शिकायत दर्ज करें।",
    },
    {
        "id": "grievance_status",
        "description_en": "You previously submitted a grievance about your PM-KISAN payment. Check the status using your registration number.",
        "description_hi": "आपने पहले अपनी पीएम-किसान भुगतान के बारे में शिकायत दर्ज की थी। स्थिति जांचें।",
    },
    {
        "id": "crop_advisory",
        "description_en": "Ask for advice about growing one of your crops — best practices, irrigation schedule, or fertilizer recommendations.",
        "description_hi": "अपनी फसल उगाने के बारे में सलाह मांगें — सर्वोत्तम प्रथाएं, सिंचाई, या उर्वरक।",
    },
    {
        "id": "pest_disease",
        "description_en": "You noticed some pest or disease problem on your crop. Describe the symptoms and ask for identification and treatment.",
        "description_hi": "आपने अपनी फसल पर कोई कीट या रोग की समस्या देखी है। लक्षण बताएं और उपचार पूछें।",
    },
    {
        "id": "video_search",
        "description_en": "Ask for videos related to farming techniques, crop management, or any agricultural topic.",
        "description_hi": "खेती की तकनीक या फसल प्रबंधन से संबंधित वीडियो मांगें।",
    },
    {
        "id": "multi_tool",
        "description_en": "You want to know the weather AND mandi prices for your crop. Start with weather and then ask about prices in the same conversation.",
        "description_hi": "आप मौसम और अपनी फसल के मंडी भाव दोनों जानना चाहते हैं।",
    },
    {
        "id": "term_lookup",
        "description_en": "Ask about the meaning of an agricultural term you heard — like 'NPK', 'MSP', 'drip irrigation', or a Hindi farming term.",
        "description_hi": "किसी कृषि शब्द का अर्थ पूछें जैसे 'एनपीके', 'एमएसपी', या 'ड्रिप सिंचाई'।",
    },
]
