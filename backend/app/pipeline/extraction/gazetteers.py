"""Reference data for deterministic extraction (PRD 8.1).

Everything here is auditable reference data — a maintained list, not a model.
A gazetteer hit is reproducible, explainable and defensible in court, which is
why it takes priority over a probabilistic model wherever a list can answer the
question (PRD principle P1).

In a real deployment these lists are loaded from NCRB / Bhuvan reference data
and versioned; they are inlined here so the system runs air-gapped and so every
value has a reviewable provenance in version control.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Indian states / union territories — vehicle plate prefixes
# ---------------------------------------------------------------------------

STATE_CODES: dict[str, str] = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CH": "Chandigarh", "CG": "Chhattisgarh",
    "DD": "Daman and Diu", "DL": "Delhi", "DN": "Dadra and Nagar Haveli",
    "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh",
    "JK": "Jammu and Kashmir", "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala",
    "LA": "Ladakh", "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya",
    "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland",
    "OD": "Odisha", "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UK": "Uttarakhand", "WB": "West Bengal",
}

STATE_NAMES: frozenset[str] = frozenset(name.lower() for name in STATE_CODES.values())

# Major districts / cities with coordinates.  The coordinates exist because
# RAPID_MOVEMENT detection needs real ground distances (PRD 11.3), not just
# place names.
DISTRICTS: dict[str, tuple[float, float]] = {
    "mumbai": (19.0760, 72.8777), "mumbai city": (19.0760, 72.8777),
    "thane": (19.2183, 72.9781), "pune": (18.5204, 73.8567),
    "nagpur": (21.1458, 79.0882), "nashik": (19.9975, 73.7898),
    "aurangabad": (19.8762, 75.3433), "chhatrapati sambhajinagar": (19.8762, 75.3433),
    "solapur": (17.6599, 75.9064), "kolhapur": (16.7050, 74.2433),
    "delhi": (28.6139, 77.2090), "new delhi": (28.6139, 77.2090),
    "central delhi": (28.6465, 77.2197), "south delhi": (28.5355, 77.2410),
    "jaipur": (26.9124, 75.7873), "jodhpur": (26.2389, 73.0243),
    "udaipur": (24.5854, 73.7125), "kota": (25.2138, 75.8648),
    "bikaner": (28.0229, 73.3119), "ajmer": (26.4499, 74.6399),
    "ahmedabad": (23.0225, 72.5714), "surat": (21.1702, 72.8311),
    "vadodara": (22.3072, 73.1812), "rajkot": (22.3039, 70.8022),
    "gandhinagar": (23.2156, 72.6369),
    "lucknow": (26.8467, 80.9462), "kanpur": (26.4499, 80.3319),
    "varanasi": (25.3176, 82.9739), "agra": (27.1767, 78.0081),
    "meerut": (28.9845, 77.7064), "prayagraj": (25.4358, 81.8463),
    "allahabad": (25.4358, 81.8463), "ghaziabad": (28.6692, 77.4538),
    "noida": (28.5355, 77.3910), "bareilly": (28.3670, 79.4304),
    "bengaluru": (12.9716, 77.5946), "bangalore": (12.9716, 77.5946),
    "mysuru": (12.2958, 76.6394), "mysore": (12.2958, 76.6394),
    "hubli": (15.3647, 75.1240), "mangaluru": (12.9141, 74.8560),
    "chennai": (13.0827, 80.2707), "coimbatore": (11.0168, 76.9558),
    "madurai": (9.9252, 78.1198), "tiruchirappalli": (10.7905, 78.7047),
    "salem": (11.6643, 78.1460),
    "hyderabad": (17.3850, 78.4867), "warangal": (17.9784, 79.5941),
    "vizag": (17.6868, 83.2185), "visakhapatnam": (17.6868, 83.2185),
    "vijayawada": (16.5062, 80.6480), "tirupati": (13.6288, 79.4192),
    "kolkata": (22.5726, 88.3639), "howrah": (22.5958, 88.2636),
    "durgapur": (23.5204, 87.3119), "siliguri": (26.7271, 88.3953),
    "bhopal": (23.2599, 77.4126), "indore": (22.7196, 75.8577),
    "jabalpur": (23.1815, 79.9864), "gwalior": (26.2183, 78.1828),
    "ujjain": (23.1765, 75.7885),
    "patna": (25.5941, 85.1376), "gaya": (24.7914, 84.9994),
    "muzaffarpur": (26.1209, 85.3647), "darbhanga": (26.1542, 85.8918),
    "ranchi": (23.3441, 85.3096), "jamshedpur": (22.8046, 86.2029),
    "dhanbad": (23.7957, 86.4304), "bokaro": (23.6693, 86.1511),
    "bhubaneswar": (20.2961, 85.8245), "cuttack": (20.4625, 85.8828),
    "rourkela": (22.2604, 84.8536),
    "chandigarh": (30.7333, 76.7794), "ludhiana": (30.9010, 75.8573),
    "amritsar": (31.6340, 74.8723), "jalandhar": (31.3260, 75.5762),
    "patiala": (30.3398, 76.3869),
    "guwahati": (26.1445, 91.7362), "dibrugarh": (27.4728, 94.9120),
    "shillong": (25.5788, 91.8933), "imphal": (24.6637, 93.9063),
    "aizawl": (23.7271, 92.7176), "agartala": (23.8315, 91.2868),
    "kohima": (25.6751, 94.1086), "gangtok": (27.3389, 88.6065),
    "itanagar": (27.0844, 93.6053), "dehradun": (30.3165, 78.0322),
    "haridwar": (29.9457, 78.1642), "shimla": (31.1048, 77.1734),
    "srinagar": (34.0837, 74.7973), "jammu": (32.7266, 74.8570),
    "leh": (34.1526, 77.5771), "panaji": (15.4909, 73.8278),
    "goa": (15.2993, 74.1240), "kochi": (9.9312, 76.2673),
    "thiruvananthapuram": (8.5241, 76.9366), "kozhikode": (11.2588, 75.7804),
    "thrissur": (10.5276, 76.2144), "puducherry": (11.9416, 79.8083),
    "raipur": (21.2514, 81.6296), "bilaspur": (22.0797, 82.1409),
    "durg": (21.1901, 81.2849), "nanded": (19.1383, 77.3210),
    "latur": (18.4088, 76.5604), "akola": (20.7002, 77.0082),
    "amravati": (20.9374, 77.7796), "jalgaon": (21.0077, 75.5626),
    "satara": (17.6805, 73.9903), "sangli": (16.8524, 74.5815),
    "beed": (18.9891, 75.7600), "osmanabad": (18.1861, 76.0413),
    "nagaur": (27.2023, 73.7319), "alwar": (27.5530, 76.6346),
    "bharatpur": (27.2152, 77.5110), "sikar": (27.6094, 75.1398),
    "jhunjhunu": (28.1267, 75.4013), "churu": (28.2952, 74.9616),
    "sriganganagar": (29.9238, 73.8780), "ganganagar": (29.9238, 73.8780),
}

# ---------------------------------------------------------------------------
# IPC sections most commonly encountered in FIRs
# ---------------------------------------------------------------------------

IPC_SECTIONS: dict[str, str] = {
    "120B": "Criminal conspiracy", "121": "Waging war against the Government of India",
    "124A": "Sedition", "143": "Unlawful assembly", "147": "Rioting",
    "148": "Rioting armed with deadly weapon", "149": "Unlawful assembly guilty of offence",
    "153A": "Promoting enmity between groups", "166": "Public servant disobeying law",
    "167": "Public servant framing incorrect document", "170": "Personating a public servant",
    "171": "Bribery", "188": "Disobedience to order duly promulgated",
    "201": "Causing disappearance of evidence", "212": "Harbouring offender",
    "216": "Harbouring offender who has escaped", "217": "Public servant disobeying direction",
    "218": "Public servant framing incorrect record", "224": "Resistance to lawful apprehension",
    "228": "Intentional insult or interruption to public servant",
    "269": "Negligent act likely to spread infection",
    "272": "Adulteration of food or drink intended for sale",
    "273": "Sale of noxious food or drink",
    "279": "Rash driving or riding on a public way",
    "284": "Negligent conduct with respect to poisonous substance",
    "285": "Negligent conduct with respect to fire or combustible matter",
    "290": "Public nuisance", "294": "Obscene acts and songs",
    "295": "Injuring or defiling place of worship", "297": "Trespassing on burial places",
    "298": "Uttering words with deliberate intent to wound religious feelings",
    "299": "Culpable homicide", "300": "Murder", "302": "Punishment for murder",
    "304": "Punishment for culpable homicide not amounting to murder",
    "304B": "Dowry death", "306": "Abetment of suicide",
    "307": "Attempt to murder", "308": "Attempt to commit culpable homicide",
    "309": "Attempt to commit suicide", "312": "Causing miscarriage",
    "319": "Hurt", "323": "Punishment for voluntarily causing hurt",
    "324": "Voluntarily causing hurt by dangerous weapons",
    "325": "Punishment for voluntarily causing grievous hurt",
    "326": "Voluntarily causing grievous hurt by dangerous weapons",
    "332": "Voluntarily causing hurt to deter public servant",
    "341": "Punishment for wrongful restraint",
    "342": "Punishment for wrongful confinement",
    "345": "Wrongful confinement of person for whose liberation writ has been issued",
    "346": "Wrongful confinement in secret",
    "347": "Wrongful confinement to extort property or constrain to illegal act",
    "352": "Punishment for assault or criminal force otherwise than on grave provocation",
    "353": "Assault or criminal force to deter public servant",
    "354": "Assault or criminal force to woman with intent to outrage her modesty",
    "354A": "Sexual harassment", "354B": "Assault or use of criminal force to woman with intent to disrobe",
    "354C": "Voyeurism", "354D": "Stalking", "356": "Assault or criminal force in attempt to commit theft",
    "363": "Punishment for kidnapping", "364": "Kidnapping or abducting in order to murder",
    "364A": "Kidnapping for ransom", "365": "Kidnapping or abducting with intent secretly and wrongfully to confine",
    "366": "Kidnapping or abducting woman to compel her marriage",
    "366A": "Procuration of minor girl", "367": "Kidnapping or abducting in order to subject person to grievous hurt",
    "368": "Wrongfully concealing or keeping in confinement kidnapped person",
    "370": "Trafficking of persons", "370A": "Exploitation of a trafficked person",
    "372": "Selling minor for purposes of prostitution",
    "373": "Buying minor for purposes of prostitution",
    "375": "Rape", "376": "Punishment for rape", "376A": "Punishment for causing death or persistent vegetative state",
    "376AB": "Rape on woman under twelve years of age",
    "376D": "Gang rape", "376E": "Punishment for repeat offenders",
    "377": "Unnatural offences", "384": "Punishment for extortion",
    "385": "Putting person in fear of injury in order to commit extortion",
    "386": "Extortion by putting a person in fear of death or grievous hurt",
    "387": "Putting person in fear of death or grievous hurt in order to commit extortion",
    "389": "Putting person in fear of accusation of offence in order to commit extortion",
    "390": "Robbery and dacoity — robbery", "392": "Punishment for robbery",
    "394": "Voluntarily causing hurt in committing robbery",
    "395": "Punishment for dacoity", "396": "Dacoity with murder",
    "397": "Robbery or dacoity with attempt to cause death or grievous hurt",
    "398": "Attempt to commit robbery or dacoity when armed with deadly weapon",
    "399": "Making preparation to commit dacoity", "400": "Punishment for belonging to gang of dacoits",
    "401": "Punishment for belonging to gang of thieves",
    "402": "Assembling for purpose of committing dacoity",
    "403": "Dishonest misappropriation of property",
    "404": "Dishonest misappropriation of property possessed by deceased person",
    "405": "Criminal breach of trust", "406": "Punishment for criminal breach of trust",
    "407": "Criminal breach of trust by carrier etc.",
    "408": "Criminal breach of trust by clerk or servant",
    "409": "Criminal breach of trust by public servant or banker etc.",
    "411": "Dishonestly receiving stolen property",
    "412": "Dishonestly receiving property stolen in the commission of a dacoity",
    "413": "Habitually dealing in stolen property",
    "414": "Assisting in concealment of stolen property",
    "415": "Cheating", "417": "Punishment for cheating",
    "418": "Cheating with knowledge that wrongful loss may ensue",
    "419": "Punishment for cheating by personation",
    "420": "Cheating and dishonestly inducing delivery of property",
    "421": "Dishonest or fraudulent removal or concealment of property",
    "423": "Dishonest or fraudulent execution of deed of transfer",
    "424": "Dishonest or fraudulent removal or concealment of property",
    "425": "Mischief", "426": "Punishment for mischief",
    "427": "Mischief causing damage to the amount of fifty rupees",
    "428": "Mischief by killing or maiming animal",
    "429": "Mischief by killing or maiming cattle",
    "430": "Mischief by injury to works of irrigation",
    "435": "Mischief by fire or explosive substance with intent to cause damage",
    "436": "Mischief by fire or explosive substance with intent to destroy house",
    "440": "Mischief committed after preparation made for causing death or hurt",
    "441": "Criminal trespass", "447": "Punishment for criminal trespass",
    "448": "Punishment for house-trespass",
    "449": "House-trespass in order to commit offence punishable with death",
    "450": "House-trespass in order to commit offence punishable with imprisonment for life",
    "451": "House-trespass in order to commit offence punishable with imprisonment",
    "452": "House-trespass after preparation for hurt, assault or wrongful restraint",
    "454": "Lurking house-trespass or house-breaking in order to commit offence",
    "457": "Lurking house-trespass or house-breaking by night in order to commit offence",
    "461": "Dishonestly breaking open receptacle containing property",
    "465": "Punishment for forgery", "467": "Forgery of valuable security, will etc.",
    "468": "Forgery for purpose of cheating",
    "471": "Using as genuine a forged document",
    "474": "Having possession of document described in section 466 or 467 knowing it to be forged",
    "489A": "Counterfeiting currency notes or bank notes",
    "489B": "Using as genuine, forged or counterfeit currency notes or bank notes",
    "489C": "Possession of forged or counterfeit currency notes or bank notes",
    "489D": "Making or possessing instruments or materials for forging currency",
    "489E": "Making or using documents resembling currency notes or bank notes",
    "493": "Cohabitation caused by a man deceitfully inducing a belief of lawful marriage",
    "494": "Marrying again during lifetime of husband or wife",
    "495": "Same offence with concealment of former marriage from person with whom subsequent marriage is contracted",
    "496": "Marriage ceremony fraudulently gone through without lawful marriage",
    "497": "Adultery", "498": "Enticing or taking away or detaining with criminal intent a married woman",
    "498A": "Husband or relative of husband of a woman subjecting her to cruelty",
    "503": "Criminal intimidation", "504": "Intentional insult with intent to provoke breach of the peace",
    "505": "Statements conducing to public mischief",
    "506": "Punishment for criminal intimidation",
    "507": "Criminal intimidation by an anonymous communication",
    "509": "Word, gesture or act intended to insult the modesty of a woman",
    "511": "Punishment for attempting to commit offences",
}

# ---------------------------------------------------------------------------
# Person-name support data for the probabilistic stage
# ---------------------------------------------------------------------------

COMMON_INDIAN_FIRST_NAMES: frozenset[str] = frozenset(
    """
    aarav aarush advait ajay ajeet akash akshay amar amit amrit anand anil ankit anuj arjun
    arun arvind ashok ashwin avinash babu balwant bhavesh bhupesh bikram binod bipin chandan
    chandra chetan chirag daljit damodar dayal deepak deepti devender dhanraj dharma dinesh
    dinesh dinkar dipak dushyant gajendra ganpat gaurav girish gopal gourav govind gulab
    hansraj harish harjit hariom hemant himanshu imran indra ishwar jagdish jagmohan jai
    jatin jayant jitender jitendra joginder kamal kanhaiya kapil karan karanveer kartik
    kashinath kedar kishan krishna kuldeep kunal laxman lokesh madan mahesh mahendra manish
    manoj mohan mohit mukesh munna nagesh naresh narinder naveen navin neeraj nikhil nilesh
    nirmal omprakash pankaj parveen pawan pradeep prakash prashant praveen prem premchand
    rahul raj rajan rajat rajesh rajinder rajiv rajkumar rakesh ramesh ram ramdas ranbir
    randhir ranjit ravi ravinder rohit roshan sachin sagar sahil sanjay sanjeev santosh
    satish satyam saurabh shakti shankar sharad sharma sher shekhar shiv shivam shyam
    sandeep sandip subhash sudhir sujeet sujit suresh sushil tara tarun tej tejpal umesh
    upendra uttam vasant vicky vidyadhar vijay vikas vikram vinay vinod vipin virender
    vishal vishnu yadav yash yashwant yogesh yuvraj
    anita anjali anju anshu anupama aparna archana arti asha asha bharti bindu chanchal
    chandrakala deepa devi dimple dipti gauri geeta girja hema indira indu jaya jyoti
    kaajal kajal kalpana kamla kanta karuna kavita kiran kirti kusum lata laxmi madhu
    madhuri maha manju manjula meena meera mira monica mohini nalini namita nandini neelam
    neha nisha nirmala padmini pallavi pankhuri pooja prabha pramila priyanka pushpa radha
    rajni rakhi rani rashi reena rekha renu rita ruchi rupali sadhana sandhya sangita
    sarita saroj savita seema shakuntala shanti sharda sheela shilpa shobha shreya shweta
    sita skruti sneha sri suman sumitra sunanda sunita sunita sushila swati tanuja tulsi
    uma urmila usha vandana veena vidya vindhu
    """.split()
)

# Tokens that look like proper nouns in a FIR but are not people.
PERSON_STOPWORDS: frozenset[str] = frozenset(
    """
    fir police station thane thana district state india government court high supreme
    session additional chief judicial magistrate inspector sub inspector constable head
    constable officer investigating officer io asi si hc cpi complainant accused witness
    victim deceased injured statement report case crime number section sections act ipc
    crpc evidence document annexure dated date time morning evening night afternoon
    monday tuesday wednesday thursday friday saturday sunday january february march april
    may june july august september october november december at on in the of and to from
    by with near opposite behind front road street nagar colony chowk bazar market hospital
    bank branch company limited pvt ltd private telephone mobile number vehicle car bike
    truck taxi auto rickshaw train railway bus stand airport amount rupees rs inr cash
    transfer account ifsc aadhaar pan card address village tehsil taluka block ward
    circle zone range reserve forest river canal bridge temple mosque church gurudwara
    school college university hostel hotel lodge guest house flat building floor room
    shop godown warehouse factory mill industry mill compound gate no yes sir madam
    statement recorded under section forwarded submitted respectfully yours faithfully
    """.split()
)

# Relation cues used by the probabilistic/relation stage.
RELATION_CUES: tuple[tuple[str, str, float], ...] = (
    (r"\barrested (?:along )?with\b", "arrested_with", 0.75),
    (r"\bapprehended (?:along )?with\b", "arrested_with", 0.7),
    (r"\baccompanied by\b", "associate_of", 0.6),
    (r"\balong with\b", "associate_of", 0.55),
    (r"\bin the company of\b", "associate_of", 0.55),
    (r"\bassociate(?:s)? of\b", "associate_of", 0.7),
    (r"\bknown associate of\b", "associate_of", 0.75),
    (r"\baccomplice(?:s)? of\b", "named_accomplice_of", 0.8),
    (r"\bconfederate of\b", "named_accomplice_of", 0.7),
    (r"\bson of\b", "relative_of", 0.8),
    (r"\bdaughter of\b", "relative_of", 0.8),
    (r"\bwife of\b", "relative_of", 0.8),
    (r"\bhusband of\b", "relative_of", 0.8),
    (r"\bbrother of\b", "relative_of", 0.75),
    (r"\bsister of\b", "relative_of", 0.75),
    (r"\bfather of\b", "relative_of", 0.8),
    (r"\bmother of\b", "relative_of", 0.8),
    (r"\bcousin of\b", "relative_of", 0.7),
    (r"\bnephew of\b", "relative_of", 0.7),
    (r"\buncle of\b", "relative_of", 0.7),
    (r"\brelative of\b", "relative_of", 0.65),
    (r"\bbusiness partner of\b", "associate_of", 0.7),
    (r"\bpartner of\b", "associate_of", 0.6),
    (r"\bdriver of\b", "associate_of", 0.5),
    (r"\bmet with\b", "associate_of", 0.5),
    (r"\bwas seen with\b", "associate_of", 0.5),
    (r"\bcontacted\b", "associate_of", 0.45),
    (r"\bknown to\b", "associate_of", 0.4),
    (r"\bgang(?:ster)? of\b", "member_of", 0.6),
    (r"\bmember of\b", "member_of", 0.7),
    (r"\bleader of\b", "member_of", 0.7),
)

# Hindi / romanised-Hindi equivalents (transliterated comparison at match time).
RELATION_CUES_HI: tuple[tuple[str, str, float], ...] = (
    (r"के साथ गिरफ्तार", "arrested_with", 0.75),
    (r"साथ में", "associate_of", 0.5),
    (r"का पुत्र", "relative_of", 0.8),
    (r"की पत्नी", "relative_of", 0.8),
    (r"का भाई", "relative_of", 0.75),
    (r"की बहन", "relative_of", 0.75),
    (r"का सदस्य", "member_of", 0.7),
    (r"का साथी", "associate_of", 0.6),
    (r"उर्फ", "alias", 0.7),
)

ORG_SUFFIXES: tuple[str, ...] = (
    "limited", "ltd", "pvt", "private", "llp", "inc", "company", "co", "group",
    "syndicate", "gang", "sangh", "sena", "trust", "foundation", "society",
    "finance", "fintech", "enterprises", "traders", "exports", "logistics",
    "transport", "construction", "builders", "developers", "motors", "agency",
)

# Tokens that mark an *organisation* rather than a natural person.  Kept
# separate from ORG_SUFFIXES because the deterministic organisation extractor
# builds a regex out of ORG_SUFFIXES and must stay narrow.
ORG_NAME_TOKENS: frozenset[str] = frozenset(
    """
    traders trader enterprises enterprise industries industry stores store
    emporium jewellers jewelers textiles garments motors services solutions
    ventures corporation corp associates mandal vyapar udyog bhandar
    kirana medicals pharmaceuticals agencies agency bureau
    """.split()
)

# Vehicle makes/models — a person-shaped span that is really a vehicle.
VEHICLE_TOKENS: frozenset[str] = frozenset(
    """
    scorpio bolero innova fortuner harrier creta swift alto wagonr wagon
    i10 i20 baleno dzire tiago nexon punch honda hero tata maruti suzuki
    toyota mahindra bajaj hyundai ford renault nissan kia mg jeep bmw audi
    pulsar splendor platina activa dio access shine unicorn apache
    scooter motorcycle car jeep truck tempo
    """.split()
)

# Devanagari function words, FIR boilerplate and case vocabulary.  A candidate
# name containing any of these is not a person — this list is what keeps the
# offline fallback from turning every Hindi sentence into two dozen "people".
HINDI_STOPWORDS: frozenset[str] = frozenset(
    """
    के का की को में मे से ने पर और या था थी थे हैं है हूँ हो गया गई गए कर
    करता करते करती किया किए कराई कराया करवाया दिया दिए दी लिया लिये लिए होने
    हुआ हुई हुए कि इस उस उसे उसके इसके इन उन्होंने मैं मुझे हम आप वह वे
    आरोपी आरोपीगण आरोपियों अभियुक्त शिकायतकर्ता शिकायत परिवादी गवाह साक्षी
    पीड़ित मृतक घायल निरीक्षक उपनिरीक्षक सिपाही हेडकांस्टेबल थाना थाने
    पुलिस पुलिस स्टेशन विवेचना विवेचक अदालत न्यायालय कोर्ट मजिस्ट्रेट
    प्रथम सूचना रिपोर्ट रपट दिनांक तारीख समय बजे रात रात्रि दिन सुबह शाम
    धारा धाराएं अधिनियम संख्या नंबर नम्बर क्रमांक पंजीकरण रजिस्ट्रेशन
    मोबाइल फोन कॉल काल बुलाया फोन संदेश संदेशों खाता खाते बैंक बैंकों
    राशि रकम रुपये रुपए लाख करोड़ हजार जमा ट्रांसफर ट्रांसफर लेनदेन लेन देन
    हस्ताक्षर दस्तखत दर्ज कर्ता पंजीकृत सूचना सूचित निवेदन प्रतिवेदन
    घटना वारदात मामला मामले मामलों समय स्थान जगह स्थित अवस्थित निवासी
    निवास रहनेवाला पता उम्र आयु वर्ष साल बरस लगभग करीब तकरीबन
    प्रोपराइटर मालिक दुकान दुकानदार व्यापार व्यवसाय कारोबार
    मांग मांग की वसूली रंगदारी धमकी धमकाया अपहरण हत्या चोरी डकैती लूट
    गिरोह गैंग संगठित सदस्य सदस्यों सरगना नेता सहयोगी साथी साथियों
    पत्नी पति पुत्र पुत्री बेटा बेटी भाई बहन बहनों भाइयों माता पिता
    उर्फ ऊर्फ उर्फ अलियास
    सफेद काला नीला लाल पीला हरा
    महिंद्रा स्कॉर्पियो स्कार्पियो आरजे एबी सीडी पीजे
    ट्रेडर्स ट्रेडर व्यापार मंडल वेलफेयर कल्याण समिति
    साथ नाते माध्यम तहत द्वारा अनुसार अंतर्गत बाद पहले बीच दौरान
    तथा एवं अथवा कुल अलग अन्य सभी कुछ जिसके जिन्होंने जहां जब तब
    सक्रिय संलिप्त पाया पाई पाए जुड़ा जुड़ी शामिल आया आए गई
    राजस्थान जयपुर जोधपुर कोटा उदयपुर बीकानेर अजमेर सांगानेर बापू नगर
    शहर रोड मार्ग चौराहा बाजार मंडी कॉलोनी
    """.split()
)

# Hindi titles / role words.  These are *transparent* in a name window: they
# precede the name (आरोपी रमेश यादव) and are dropped rather than rejected.
HINDI_ROLE_TOKENS: frozenset[str] = frozenset(
    """
    श्री श्रीमती श्रीमान सुश्री कुमारी कुमार श्रीमतीजी स्वर्गीय
    आरोपी आरोपियों अभियुक्त शिकायतकर्ता परिवादी गवाह साक्षी पीड़ित
    निरीक्षक उपनिरीक्षक सिपाही थानेदार दरोगा प्रोपराइटर मालिक
    पत्नी पति पुत्र पुत्री बेटा बेटी भाई बहन माता पिता
    निवासी रहनेवाला उम्र आयु वर्ष साल
    """.split()
)

# English function words not covered by PERSON_STOPWORDS.
EXTRA_ENGLISH_STOPWORDS: frozenset[str] = frozenset(
    """
    one two three four five six seven eight nine ten first second third
    this that these those his her their our my its also said says told
    came went gone gave given taken took made make makes making
    city town district police station reported registered lodged
    above below under over between through during after before
    proprietor owner trader businessman shopkeeper
    white black blue red green yellow
    """.split()
)

BANK_HINTS: tuple[str, ...] = (
    "bank", "state bank", "punjab national bank", "hdfc", "icici", "axis", "kotak",
    "union bank", "canara", "bank of baroda", "bank of india", "idbi", "yes bank",
    "indusind", "uco", "indian bank", "central bank", "karnataka bank",
)

# Telecom CDR schema registry (PRD 7): per-operator column aliases.
CDR_SCHEMAS: dict[str, dict[str, tuple[str, ...]]] = {
    "JIO_CDR_V2": {
        "caller": ("calling_number", "caller", "a_party", "a_number", "from"),
        "callee": ("called_number", "callee", "b_party", "b_number", "to"),
        "timestamp": ("call_date", "timestamp", "call_time", "date_time", "event_time"),
        "duration": ("duration", "call_duration", "duration_sec", "duration_s", "dur"),
        "direction": ("call_type", "direction", "call_direction", "type"),
        "imei": ("imei", "device_imei", "calling_imei"),
        "cell_id": ("cell_id", "cellid", "first_cell_id", "lac_cell"),
    },
    "AIRTEL_2023": {
        "caller": ("a_party_msisdn", "calling_party", "msisdn_a", "caller"),
        "callee": ("b_party_msisdn", "called_party", "msisdn_b", "callee"),
        "timestamp": ("event_date_time", "call_start_time", "timestamp"),
        "duration": ("call_duration_secs", "duration", "call_duration"),
        "direction": ("call_direction", "direction", "in_out_flag"),
        "imei": ("imei_no", "imei"),
        "cell_id": ("first_cell_id", "cell_id"),
    },
    "GENERIC": {
        "caller": ("caller", "calling_number", "a_party", "a_number", "from", "from_number",
                   "msisdn_a", "calling_party", "a_party_msisdn"),
        "callee": ("callee", "called_number", "b_party", "b_number", "to", "to_number",
                   "msisdn_b", "called_party", "b_party_msisdn"),
        "timestamp": ("timestamp", "call_date", "call_time", "date_time", "event_time",
                      "call_start_time", "event_date_time", "date"),
        "duration": ("duration", "duration_s", "duration_sec", "call_duration",
                     "call_duration_secs", "dur"),
        "direction": ("direction", "call_type", "call_direction", "in_out_flag", "type"),
        "imei": ("imei", "imei_no", "device_imei", "calling_imei"),
        "cell_id": ("cell_id", "cellid", "first_cell_id", "lac_cell"),
    },
}

SOCIAL_PLATFORM_SIGNATURES: dict[str, tuple[str, ...]] = {
    "facebook": ("friends", "timeline", "wall", "facebook", "fbid"),
    "whatsapp": ("whatsapp", "wa_", "chat"),
    "twitter": ("tweet", "followers", "twitter", "handle"),
    "instagram": ("instagram", "followers", "reels"),
    "telegram": ("telegram", "channel", "chat_id"),
}
