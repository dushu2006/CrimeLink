"""Name/address gazetteers for the synthetic corpus.

These are drawn from common Indian given names, surnames, district/state
names, street names and organisation suffixes.  They are intentionally
generic and represent no specific real individuals or entities.
"""

from __future__ import annotations

# Common Indian given names (Hindi belt / pan-Indian) — mix of male/female/unisex
GIVEN_NAMES_M = [
    "Ramesh", "Suresh", "Mahesh", "Dinesh", "Rajesh", "Mukesh", "Rakesh",
    "Vikram", "Arjun", "Rohit", "Amit", "Sumit", "Anil", "Sunil", "Manoj",
    "Deepak", "Sanjay", "Ajay", "Vijay", "Vinod", "Prakash", "Pradeep",
    "Ashok", "Kishore", "Naresh", "Ravi", "Rahul", "Sachin", "Gaurav",
    "Puneet", "Harish", "Jagdish", "Lokesh", "Naveen", "Nitin", "Karan",
    "Kamal", "Brijesh", "Yogesh", "Hemant", "Chetan", "Shyam", "Shivam",
    "Abhishek", "Aditya", "Akshay", "Ankit", "Bharat", "Bhupendra",
]

GIVEN_NAMES_F = [
    "Sunita", "Suman", "Radha", "Meena", "Geeta", "Sarita", "Anita",
    "Pooja", "Priya", "Neha", "Sneha", "Kavita", "Manju", "Rekha",
    "Shweta", "Smriti", "Aarti", "Anjali", "Divya", "Kiran", "Kanchan",
    "Lakshmi", "Maya", "Nisha", "Poonam", "Rashmi", "Ritu", "Sangeeta",
    "Seema", "Shalini", "Shanti", "Sushma", "Vandana", "Varsha", "Annu",
    "Babita", "Deepa", "Jaya", "Mamta", "Rina", "Sadhana", "Savita",
    "Shobha", "Usha", "Hema", "Alka", "Bhavna", "Chhaya", "Deepti",
]

SURNAMES = [
    "Kumar", "Singh", "Sharma", "Verma", "Gupta", "Yadav", "Patel",
    "Jha", "Pandey", "Mishra", "Tiwari", "Chauhan", "Rathore", "Mehta",
    "Shah", "Desai", "Choudhary", "Reddy", "Naidu", "Iyer", "Agarwal",
    "Bansal", "Goel", "Goyal", "Jain", "Khandelwal", "Mathur", "Saxena",
    "Bhatt", "Joshi", "Trivedi", "Bose", "Das", "Ghosh", "Sen", "Roy",
    "Thakur", "Rawat", "Negi", "Bisht", "Rana", "Chand", "Khan", "Ali",
    "Shaikh", "Ahmed", "Siddiqui", "Hussain", "Mohammed",
]

FATHER_HONORIFICS = ["s/o", "S/O", "son of", "s/o Shri", "पुत्र", "पुत्र श्री"]
SPOUSE_HONORIFICS = ["w/o", "W/O", "wife of", "w/o Shri", "पत्नी श्री", "पत्नी"]

INDIAN_STATES = [
    ("RJ", "Rajasthan"),
    ("UP", "Uttar Pradesh"),
    ("MP", "Madhya Pradesh"),
    ("MH", "Maharashtra"),
    ("DL", "Delhi"),
    ("HR", "Haryana"),
    ("PB", "Punjab"),
    ("GJ", "Gujarat"),
    ("KA", "Karnataka"),
    ("TN", "Tamil Nadu"),
    ("TS", "Telangana"),
    ("AP", "Andhra Pradesh"),
    ("BR", "Bihar"),
    ("WB", "West Bengal"),
]

DISTRICTS = [
    "Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer", "Bikaner", "Alwar",
    "Sikar", "Bharatpur", "Tonk", "Lucknow", "Kanpur", "Varanasi", "Agra",
    "Meerut", "Ghaziabad", "Noida", "Bhopal", "Indore", "Gwalior", "Jabalpur",
    "Mumbai", "Pune", "Nagpur", "Nashik", "Aurangabad", "Delhi", "New Delhi",
    "Gurgaon", "Faridabad", "Panipat", "Amritsar", "Ludhiana", "Chandigarh",
    "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bengaluru", "Mysuru",
    "Hyderabad", "Chennai", "Coimbatore", "Madurai", "Patna", "Muzaffarpur",
    "Kolkata", "Howrah", "Siliguri", "Visakhapatnam", "Vijayawada",
]

STREET_WORDS = [
    "Station Road", "MG Road", "Bazaar", "Chowk", "Colony", "Nagar", "Vihar",
    "Marg", "Enclave", "Sector", "Phase", "Block", "Mohalla", "Basti",
    "Market Road", "Civil Lines", "Lal Quarter", "Gandhi Nagar", "Nehru Road",
    "Shastri Nagar", "Ambedkar Colony",
]

OCCUPATIONS = [
    "Shopkeeper", "Auto driver", "Truck driver", "Taxi driver", "Mechanic",
    "Laborer", "Farmer", "Tailor", "Carpenter", "Painter", "Electrician",
    "Plumber", "Small business owner", "Restaurant worker", "Delivery worker",
    "Security guard", "Clerk", "Real estate broker", "Jeweller", "Contractor",
    "Unemployed", "Student", "Housewife", "Retired", "Travel agent",
    "Mobile phone repair", "Tea stall owner", "Kabadiwala",
]

ORG_SUFFIXES = [
    "Traders", "Enterprises", "Trading Co.", "Construction", "Logistics",
    "Finance", "Investments", "Motors", "Garage", "Communication",
    "Telecom", "Marketing", "Imports", "Exports", "Industries",
]

ORG_PREFIXES = [
    "Shree", "Jai", "Om", "Maa", "Ganesh", "Balaji", "Sai", "New",
    "Royal", "Star", "Sunrise", "Golden", "National", "Reliable",
]

BANK_IFSC_PREFIXES = [
    "SBIN", "PUNB", "BARB", "HDFC", "ICIC", "UTIB", "CNRB", "BKID",
    "IDIB", "MAHB", "KKBK", "YESB",
]
BANK_NAMES = [
    "State Bank of India", "Punjab National Bank", "Bank of Baroda",
    "HDFC Bank", "ICICI Bank", "Axis Bank", "Canara Bank",
    "Bank of India", "Indian Bank", "Bank of Maharashtra",
    "Kotak Mahindra Bank", "Yes Bank",
]

RTO_CODES = [
    "RJ14", "RJ01", "RJ05", "RJ07", "UP14", "UP16", "DL1C", "DL3C",
    "HR26", "HR01", "PB10", "MH01", "MH02", "MH12", "MH14", "KA01",
    "KA03", "KA05", "TN01", "TN09", "TS07", "TS08", "AP28", "GJ01",
    "MP09", "MP10", "BR01", "WB02",
]

IPC_SECTIONS = [
    "302", "307", "384", "386", "392", "395", "420", "467", "468", "471",
    "120B", "34", "147", "148", "149", "323", "324", "354", "379", "380",
    "406", "409", "411", "420", "452", "465", "489A", "504", "506",
]

CASE_TYPES = [
    "Extortion", "Robbery", "Cheating / Fraud", "Vehicle theft",
    "Chain snatching", "Criminal intimidation", "Arms Act",
    "Narcotic Drugs and Psychotropic Substances", "Cyber financial fraud",
    "Kidnapping for ransom",
]
