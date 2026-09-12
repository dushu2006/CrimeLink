/**
 * Multilingual labels for CrimeLink:
 * Supports English (en), Hindi (hi), Telugu (te), and Tamil (ta).
 *
 * All translation keys are centralized here for type-safety and consistency
 * across all law-enforcement and intelligence workflows.
 */

export type Lang = "en" | "hi" | "te" | "ta";

export interface LangInfo {
  code: Lang;
  label: string;
  native: string;
}

export const SUPPORTED_LANGS: LangInfo[] = [
  { code: "en", label: "EN", native: "English" },
  { code: "hi", label: "HI", native: "हिंदी" },
  { code: "te", label: "TE", native: "తెలుగు" },
  { code: "ta", label: "TA", native: "தமிழ்" },
];

export const STRINGS: Record<string, Record<Lang, string>> = {
  // App brand & header
  "app.title": {
    en: "CrimeLink",
    hi: "क्राइमलिंक",
    te: "క్రైమ్‌లింక్",
    ta: "கிரைம்லிங்க்",
  },
  "app.subtitle": {
    en: "Criminal Network Analysis — Ministry of Home Affairs / NCRB",
    hi: "आपराधिक नेटवर्क विश्लेषण — गृह मंत्रालय / एनसीआरबी",
    te: "నేర నెట్‌వర్క్ విశ్లేషణ — హోం మంత్రిత్వ శాఖ / NCRB",
    ta: "குற்றவியல் பிணைய பகுப்பாய்வு — உள்துறை அமைச்சகம் / NCRB",
  },

  // Navigation sections & links
  "nav.sectionOperations": {
    en: "OPERATIONS",
    hi: "संचालन",
    te: "కార్యకలాపాలు",
    ta: "செயல்பாடுகள்",
  },
  "nav.sectionIntelligence": {
    en: "INTELLIGENCE & HITL",
    hi: "इंटेलिजेंस और एचआईटीएल",
    te: "ఇంటెలిజెన్స్ & హెచ్.ఐ.టి.ఎల్",
    ta: "புலனாய்வு & மனித ஆய்வு",
  },
  "nav.sectionGovernance": {
    en: "GOVERNANCE",
    hi: "शासन और प्रशासन",
    te: "పాలన & పర్యవేక్షణ",
    ta: "நிர்வாகம் & ஆளுகை",
  },
  "nav.cases": {
    en: "Cases",
    hi: "प्रकरण",
    te: "కేసులు",
    ta: "வழக்குகள்",
  },
  "nav.graph": {
    en: "Investigation Graph",
    hi: "जाँच नेटवर्क ग्राफ़",
    te: "దర్యాప్తు గ్రాఫ్",
    ta: "விசாரணை வரைபடம்",
  },
  "nav.entities": {
    en: "Entity Directory",
    hi: "इकाई निर्देशिका",
    te: "ఎంటిటీ డైరెక్టరీ",
    ta: "நிறுவனங்கள் பட்டியல்",
  },
  "nav.relationships": {
    en: "Relationships",
    hi: "संबंध",
    te: "సంబంధాలు",
    ta: "உறவுகள் / தொடர்புகள்",
  },
  "nav.sources": {
    en: "Raw Sources",
    hi: "मूल स्रोत",
    te: "ముడి ఆధారాలు",
    ta: "மூல ஆவணங்கள்",
  },
  "nav.ai": {
    en: "AI Investigation",
    hi: "एआई जाँच",
    te: "కృత్రిమ మేధ దర్యాప్తు",
    ta: "AI புலனாய்வு",
  },
  "nav.reasoning": {
    en: "Investigation Analysis",
    hi: "जाँच विश्लेषण",
    te: "దర్యాప్తు విశ్లేషణ",
    ta: "புலனாய்வு பகுப்பாய்வு",
  },
  "inv.title": {
    en: "Investigation Analysis",
    hi: "जाँच विश्लेषण",
    te: "దర్యాప్తు విశ్లేషణ",
    ta: "புலனாய்வு பகுப்பாய்வு",
  },
  "inv.subtitle": {
    en: "Evidence-backed assessment: findings, patterns, hypotheses, contradictions, gaps and next direction.",
    hi: "साक्ष्य-आधारित आकलन: निष्कर्ष, पैटर्न, परिकल्पनाएँ, विरोधाभास, कमियाँ और अगली दिशा।",
    te: "సాక్ష్యాధారిత అంచనా: కనుగొన్నవి, నమూనాలు, ఊహలు, వైరుధ్యాలు, లోటుపాట్లు, తదుపరి దిశ.",
    ta: "சான்று அடிப்படையிலான மதிப்பீடு: கண்டுபிடிப்புகள், வடிவங்கள், கருதுகோள்கள், முரண்பாடுகள், இடைவெளிகள், அடுத்த திசை.",
  },
  "inv.objective": {
    en: "Investigation objective",
    hi: "जाँच का उद्देश्य",
    te: "దర్యాప్తు లక్ష్యం",
    ta: "புலனாய்வு நோக்கம்",
  },
  "nav.review": {
    en: "Review queue",
    hi: "समीक्षा सूची",
    te: "సమీక్ష జాబితా",
    ta: "மதிப்பாய்வு வரிசை",
  },
  "nav.vault": {
    en: "Evidence Vault",
    hi: "साक्ष्य तिजोरी",
    te: "సాక్ష్యాధారాల నిల్వ",
    ta: "சான்றுகளின் காப்பகம்",
  },
  "nav.admin": {
    en: "Administration",
    hi: "प्रशासन",
    te: "పరిపాలన",
    ta: "நிர்வாகம்",
  },
  "nav.signout": {
    en: "Sign out",
    hi: "साइन आउट",
    te: "సైన్ అవుట్",
    ta: "வெளியேறு",
  },

  // Search & Topbar
  "search.placeholder": {
    en: "Search entity, account, or phone...",
    hi: "इकाई, खाता या फ़ोन नंबर खोजें...",
    te: "ఎంటిటీ, ఖాతా లేదా ఫోన్ నంబర్ వెతకండి...",
    ta: "நபர், கணக்கு அல்லது தொலைபேசியை தேடுக...",
  },

  // Authentication & First-time Setup
  "login.badge": {
    en: "Badge number",
    hi: "बैज नंबर",
    te: "బ్యాడ్జ్ సంఖ్య",
    ta: "பேட்ஜ் எண்",
  },
  "login.password": {
    en: "Password",
    hi: "पासवर्ड",
    te: "పాస్‌వర్డ్",
    ta: "கடவுச்சொல்",
  },
  "login.submit": {
    en: "Sign in",
    hi: "साइन इन करें",
    te: "సైన్ ఇన్",
    ta: "உள்நுழைக",
  },
  "login.heading": {
    en: "Sign in to CrimeLink",
    hi: "क्राइमलिंक में साइन इन करें",
    te: "క్రైమ్‌లింక్‌కి లాగిన్ అవ్వండి",
    ta: "கிரைம்லிங்கில் உள்நுழைக",
  },
  "login.note": {
    en: "Every action is recorded in a tamper-evident audit log.",
    hi: "हर कार्य को छेड़छाड़-साक्ष्य ऑडिट लॉग में दर्ज किया जाता है।",
    te: "ప్రతి చర్య టాంపర్-ఎవిడెంట్ ఆడిట్ లాగ్‌లో నమోదు చేయబడుతుంది.",
    ta: "ஒவ்வொரு செயலும் சிதைக்க முடியாத தணிக்கை பதிவில் பதிவு செய்யப்படுகிறது.",
  },
  "setup.heading": {
    en: "Create the first administrator",
    hi: "प्रथम प्रशासक बनाएँ",
    te: "మొదటి నిర్వాహకుడిని సృష్టించండి",
    ta: "முதல் நிர்வாகியை உருவாக்கவும்",
  },
  "setup.submit": {
    en: "Create administrator",
    hi: "प्रशासक बनाएँ",
    te: "నిర్వాహకుడిని సృష్టించండి",
    ta: "நிர்வாகியை உருவாக்கு",
  },
  "setup.note": {
    en: "This form appears only while the system has no users. The account you create is an ADMIN.",
    hi: "यह फ़ॉर्म केवल तब दिखता है जब सिस्टम में कोई उपयोगकर्ता नहीं है। बनाया गया खाता ADMIN होगा।",
    te: "సిస్టమ్‌లో వినియోగదారులు లేనప్పుడు మాత్రమే ఈ ఫారం కనిపిస్తుంది. మీరు సృష్టించే ఖాతా ADMIN అవుతుంది.",
    ta: "கணினியில் பயனர்கள் இல்லாதபோது மட்டுமே இந்த படிவம் தோன்றும். நீங்கள் உருவாக்கும் கணக்கு ADMIN ஆகும்.",
  },
  "setup.fullName": {
    en: "Full name",
    hi: "पूरा नाम",
    te: "పూర్తి పేరు",
    ta: "முழுப் பெயர்",
  },
  "setup.station": {
    en: "Station ID",
    hi: "थाना आईडी",
    te: "స్టేషన్ ఐడీ",
    ta: "காவல் நிலைய எண்",
  },
  "setup.jurisdiction": {
    en: "Jurisdiction ID",
    hi: "क्षेत्राधिकार आईडी",
    te: "అధికార పరిధి ఐడీ",
    ta: "அதிகார வரம்பு எண்",
  },
  "setup.jurisdictionHint": {
    en: "Use SYN-DEV so imported synthetic cases are visible in this account.",
    hi: "SYN-DEV का उपयोग करें ताकि आयातित सिंथेटिक प्रकरण दिखें।",
    te: "దిగుమతి చేసుకున్న సింథటిక్ కేసులు ఈ ఖాతాలో కనిపించడానికి SYN-DEVని ఉపయోగించండి.",
    ta: "இறக்குமதி செய்யப்பட்ட வழக்குகள் தெரிய SYN-DEV ஐப் பயன்படுத்தவும்.",
  },

  // Admin user & management
  "admin.createUser": {
    en: "Create user",
    hi: "उपयोगकर्ता बनाएँ",
    te: "వినియోగదారుని సృష్టించండి",
    ta: "பயனரை உருவாக்கு",
  },
  "admin.password": {
    en: "Password",
    hi: "पासवर्ड",
    te: "పాస్‌వర్డ్",
    ta: "கடவுச்சொல்",
  },
  "admin.role": {
    en: "Role",
    hi: "भूमिका",
    te: "పాత్ర",
    ta: "பங்கு / பதவி",
  },
  "admin.station": {
    en: "Station",
    hi: "थाना",
    te: "పోలీస్ స్టేషన్",
    ta: "காவல் நிலையம்",
  },

  // Cases registry & dossier
  "cases.title": {
    en: "Cases",
    hi: "प्रकरण",
    te: "కేసులు",
    ta: "வழக்குகள்",
  },
  "cases.new": {
    en: "Register case",
    hi: "नया प्रकरण",
    te: "కొత్త కేసు నమోదు",
    ta: "புதிய வழக்கு பதிவு",
  },
  "cases.number": {
    en: "Case number",
    hi: "प्रकरण संख्या",
    te: "కేసు సంఖ్య",
    ta: "வழக்கு எண்",
  },
  "cases.name": {
    en: "Title",
    hi: "शीर्षक",
    te: "శీర్షిక",
    ta: "தலைப்பு",
  },
  "cases.jurisdiction": {
    en: "Jurisdiction",
    hi: "क्षेत्राधिकार",
    te: "అధికార పరిధి",
    ta: "அதிகார வரம்பு",
  },
  "cases.documents": {
    en: "Documents",
    hi: "दस्तावेज़",
    te: "పత్రాలు",
    ta: "ஆவணங்கள்",
  },
  "cases.pending": {
    en: "Pending review",
    hi: "लंबित समीक्षा",
    te: "సమీక్ష పెండింగ్‌లో ఉంది",
    ta: "நிலுவையில் உள்ள மதிப்பாய்வு",
  },
  "cases.status": {
    en: "Status",
    hi: "स्थिति",
    te: "స్థితి",
    ta: "நிலை",
  },
  "cases.empty": {
    en: "No cases available. Import a dataset to begin.",
    hi: "कोई प्रकरण उपलब्ध नहीं है। आरंभ करने के लिए एक डेटासेट आयात करें।",
    te: "కేసులు ఏవీ అందుబాటులో లేవు. ప్రారంభించడానికి డేటాసెట్‌ను దిగుమతి చేయండి.",
    ta: "வழக்குகள் எதுவும் இல்லை. தொடங்க ஒரு தரவுத்தொகுப்பை இறக்குமதி செய்யவும்.",
  },
  "cases.activeFiles": {
    en: "Active Criminal Files",
    hi: "सक्रिय आपराधिक फ़ाइलें",
    te: "క్రియాశీల క్రిమినల్ ఫైళ్లు",
    ta: "செயலில் உள்ள குற்றவியல் கோப்புகள்",
  },
  "cases.openBadge": {
    en: "Open Cases",
    hi: "खुले प्रकरण",
    te: "ఓపెన్ కేసులు",
    ta: "திறந்த வழக்குகள்",
  },

  "case.detail": {
    en: "Case",
    hi: "प्रकरण",
    te: "కేసు",
    ta: "வழக்கு",
  },
  "case.dossier": {
    en: "Case Dossier",
    hi: "प्रकरण डॉसियर",
    te: "కేసు డాసియర్",
    ta: "வழக்கு ஆவணக் கோப்பு",
  },
  "case.activeCase": {
    en: "Active Case",
    hi: "सक्रिय प्रकरण",
    te: "క్రియాశీల కేసు",
    ta: "செயலில் உள்ள வழக்கு",
  },
  "case.upload": {
    en: "Upload document",
    hi: "दस्तावेज़ अपलोड करें",
    te: "పత్రాన్ని అప్‌లోడ్ చేయండి",
    ta: "ஆவணத்தை பதிவேற்றவும்",
  },
  "case.documents": {
    en: "Documents",
    hi: "दस्तावेज़",
    te: "పత్రాలు",
    ta: "ஆவணங்கள்",
  },
  "case.timeline": {
    en: "Timeline",
    hi: "समय-रेखा",
    te: "కాలక్రమం",
    ta: "காலவரிசை",
  },
  "case.export": {
    en: "Export PDF brief",
    hi: "पीडीएफ़ ब्रीफ़ निर्यात करें",
    te: "PDF నివేదికను ఎగుమతి చేయండి",
    ta: "PDF அறிக்கையை பதிவிறக்கு",
  },
  "case.processing": {
    en: "Processing",
    hi: "प्रसंस्करण",
    te: "ప్రాసెస్ అవుతోంది",
    ta: "செயலாக்கப்படுகிறது",
  },
  "case.openGraph": {
    en: "Open network graph",
    hi: "नेटवर्क ग्राफ खोलें",
    te: "నెట్‌వర్క్ గ్రాఫ్ తెరవండి",
    ta: "பிணைய வரைபடத்தை திற",
  },
  "case.livePolling": {
    en: "Live updates unavailable — the progress channel could not be established, so status is refreshed by polling.",
    hi: "लाइव अपडेट उपलब्ध नहीं — प्रगति चैनल स्थापित नहीं हो सका, इसलिए स्थिति पोलिंग द्वारा ताज़ा की जा रही है।",
    te: "లైవ్ అప్‌డేట్‌లు అందుబాటులో లేవు — ప్రోగ్రెస్ ఛానెల్ ఏర్పాటు కాలేదు, కాబట్టి పోలింగ్ ద్వారా తాజా చేయబడుతోంది.",
    ta: "நேரலை புதுப்பிப்புகள் கிடைக்கவில்லை — நிலை வாக்குப்பதிவு மூலம் புதுப்பிக்கப்படுகிறது.",
  },
  "case.review": {
    en: "Review queue",
    hi: "समीक्षा सूची",
    te: "సమీక్ష జాబితా",
    ta: "மதிப்பாய்வு வரிசை",
  },

  // Documents
  "doc.type": {
    en: "Type",
    hi: "प्रकार",
    te: "రకం",
    ta: "வகை",
  },
  "doc.file": {
    en: "File",
    hi: "फ़ाइल",
    te: "ఫైల్",
    ta: "கோப்பு",
  },
  "doc.language": {
    en: "Language",
    hi: "भाषा",
    te: "భాష",
    ta: "மொழி",
  },
  "doc.confidence": {
    en: "Source confidence",
    hi: "स्रोत विश्वास",
    te: "మూల విశ్వసనీయత",
    ta: "மூல நம்பகத்தன்மை",
  },
  "doc.status": {
    en: "Status",
    hi: "स्थिति",
    te: "స్థితి",
    ta: "நிலை",
  },
  "doc.hash": {
    en: "SHA-256",
    hi: "SHA-256",
    te: "SHA-256",
    ta: "SHA-256",
  },
  "doc.quarantined": {
    en: "Quarantined",
    hi: "संगरोधित",
    te: "క్వారంటైన్ చేయబడింది",
    ta: "தனிமைப்படுத்தப்பட்டது",
  },

  // Investigation Network Graph
  "graph.title": {
    en: "Network graph",
    hi: "नेटवर्क ग्राफ",
    te: "నెట్‌వర్క్ గ్రాఫ్",
    ta: "பிணைய வரைபடம்",
  },
  "graph.modes": {
    en: "Graph view",
    hi: "ग्राफ़ दृश्य",
    te: "గ్రాఫ్ వీక్షణ",
    ta: "வரைபடக் காட்சி",
  },
  "graph.modePerson": {
    en: "Person Graph",
    hi: "व्यक्ति ग्राफ़",
    te: "వ్యక్తి గ్రాఫ్",
    ta: "நபர் வரைபடம்",
  },
  "graph.modeMaster": {
    en: "Master Graph",
    hi: "मास्टर ग्राफ़",
    te: "మాస్టర్ గ్రాఫ్",
    ta: "முதன்மை வரைபடம்",
  },
  "graph.modeTemporal": {
    en: "Temporal Graph",
    hi: "समय-ग्राफ़",
    te: "కాలక్రమ గ్రాఫ్",
    ta: "காலவரிசை வரைபடம்",
  },
  "graph.modeHint": {
    en: "Three views over one dataset: a focused person graph, the full case network, and a time-constrained view.",
    hi: "एक डेटासेट पर तीन दृश्य: केंद्रित व्यक्ति ग्राफ़, पूरा प्रकरण नेटवर्क, और समय-सीमित दृश्य।",
    te: "ఒకే డేటాసెట్‌పై మూడు వీక్షణలు: వ్యక్తి గ్రాఫ్, పూర్తి కేసు నెట్‌వర్క్ మరియు సమయ పరిమితి వీక్షణ.",
    ta: "ஒரு தரவுத்தொகுப்பில் மூன்று காட்சிகள்: நபர் வரைபடம், முழு வழக்கு பிணையம் மற்றும் காலவரிசைக் காட்சி.",
  },
  "graph.hudTitle": {
    en: "Force Graph Canvas",
    hi: "फ़ोर्स ग्राफ़ कैनवास",
    te: "ఫోర్స్ గ్రాఫ్ కాన్వాస్",
    ta: "விசை வரைபட திரை",
  },
  "graph.nodes": {
    en: "Nodes",
    hi: "नोड्स",
    te: "నోడ్స్",
    ta: "முனையங்கள்",
  },
  "graph.edges": {
    en: "Edges",
    hi: "संबंध",
    te: "లింకులు",
    ta: "இணைப்புகள்",
  },
  "graph.zoomIn": {
    en: "Zoom In",
    hi: "बड़ा करें",
    te: "జూమ్ ఇన్",
    ta: "பெரிதாக்கு",
  },
  "graph.zoomOut": {
    en: "Zoom Out",
    hi: "छोटा करें",
    te: "జూమ్ అవుట్",
    ta: "சிறிதாக்கு",
  },
  "graph.fitScreen": {
    en: "Fit to Screen",
    hi: "स्क्रीन में फ़िट करें",
    te: "స్క్రీన్‌కు సరిపడండి",
    ta: "திரைக்கு அமை",
  },
  "graph.recenter": {
    en: "Recenter",
    hi: "पुनः केंद्रित करें",
    te: "మధ్యకు జరపండి",
    ta: "மையப்படுத்து",
  },
  "graph.masterFilters": {
    en: "Master graph filters",
    hi: "मास्टर ग्राफ़ फ़िल्टर",
    te: "మాస్టర్ గ్రాఫ్ ఫిల్టర్లు",
    ta: "முதன்மை வரைபட வடிகட்டிகள்",
  },
  "graph.filterLabels": {
    en: "Entity types",
    hi: "इकाई प्रकार",
    te: "ఎంటిటీ రకాలు",
    ta: "நிறுவன வகைகள்",
  },
  "graph.filterRelTypes": {
    en: "Relationship types",
    hi: "संबंध प्रकार",
    te: "సంబంధ రకాలు",
    ta: "உறவு வகைகள்",
  },
  "graph.includeStaging": {
    en: "Include unverified candidates",
    hi: "असत्यापित उम्मीदवार शामिल करें",
    te: "ధృవీకరించబడని అభ్యర్థులను చేర్చండి",
    ta: "சரிபார்க்கப்படாத நபர்களைச் சேர்க்கவும்",
  },
  "graph.temporalControls": {
    en: "Temporal window",
    hi: "समय-विंडो",
    te: "సమయ విండో",
    ta: "காலவரையறை சாளரம்",
  },
  "graph.temporalFrom": {
    en: "From",
    hi: "से",
    te: "నుండి",
    ta: "இருந்து",
  },
  "graph.temporalTo": {
    en: "To",
    hi: "तक",
    te: "వరకు",
    ta: "வரை",
  },
  "graph.temporalTarget": {
    en: "Focus person (optional)",
    hi: "केंद्रित व्यक्ति (वैकल्पिक)",
    te: "లక్ష్య వ్యక్తి (ఐచ్ఛికం)",
    ta: "குறிப்பிட்ட நபர் (விருப்பத்தேர்வு)",
  },
  "graph.temporalNoTarget": {
    en: "Entire case",
    hi: "पूरा प्रकरण",
    te: "మొత్తం కేసు",
    ta: "முழு வழக்கு",
  },
  "graph.temporalBuild": {
    en: "Build temporal graph",
    hi: "समय-ग्राफ़ बनाएँ",
    te: "కాలక్రమ గ్రాఫ్‌ను రూపొందించండి",
    ta: "காலவரிசை வரைபடத்தை உருவாக்கு",
  },
  "graph.temporalEmpty": {
    en: "No dated relationships in this window.",
    hi: "इस समय-विंडो में कोई दिनांकित संबंध नहीं है।",
    te: "ఈ సమయ వ్యవధిలో సంబంధాలు లేవు.",
    ta: "இந்த காலவரையறையில் உறவுகள் இல்லை.",
  },
  "graph.timeline": {
    en: "Event timeline",
    hi: "घटना समय-रेखा",
    te: "ఈవెంట్ కాలక్రమం",
    ta: "நிகழ்வு காலவரிசை",
  },
  "graph.pathSearch": {
    en: "Search",
    hi: "खोजें",
    te: "వెతకండి",
    ta: "தேடுக",
  },
  "graph.noPath": {
    en: "No chronologically coherent path.",
    hi: "कोई कालानुक्रमिक रूप से सुसंगत पथ नहीं।",
    te: "కాలక్రమానుసార మార్గం కనుగొనబడలేదు.",
    ta: "காலவரிசைப்படி இணைக்கப்பட்ட பாதை இல்லை.",
  },
  "graph.emptyView": {
    en: "Nothing in this view — adjust the filters or time window.",
    hi: "इस दृश्य में कुछ नहीं — फ़िल्टर या समय-विंडो बदलें।",
    te: "ఈ వీక్షణలో ఏమీ లేదు — ఫిల్టర్‌లు లేదా సమయ వ్యవధిని సర్దుబాటు చేయండి.",
    ta: "இந்தக் காட்சியில் எதுவும் இல்லை — வடிகட்டிகளை மாற்றவும்.",
  },
  "graph.influence": {
    en: "Influence",
    hi: "प्रभाव",
    te: "ప్రభావం",
    ta: "தாக்கம் / ஆதிக்கம்",
  },
  "graph.evidence": {
    en: "Evidence",
    hi: "साक्ष्य",
    te: "సాక్ష్యం",
    ta: "சான்றுகள்",
  },
  "graph.explanation": {
    en: "Why this score?",
    hi: "यह स्कोर क्यों?",
    te: "ఈ స్కోరు ఎందుకు?",
    ta: "இந்த மதிப்பீடு ஏன்?",
  },
  "graph.expand": {
    en: "Expand",
    hi: "विस्तार करें",
    te: "విస్తరించండి",
    ta: "விரிவாக்கு",
  },
  "graph.paths": {
    en: "Temporal path search",
    hi: "समय-आधारित पथ खोज",
    te: "కాలక్రమ మార్గ శోధన",
    ta: "காலவரிசை பாதை தேடல்",
  },
  "graph.staging": {
    en: "Low-confidence candidates",
    hi: "कम-विश्वास उम्मीदवार",
    te: "తక్కువ విశ్వసనీయత గల అభ్యర్థులు",
    ta: "குறைந்த நம்பகத்தன்மை கொண்ட பதிவுகள்",
  },
  "graph.personCentricHint": {
    en: "Pick a person, then expand their network hop by hop. The graph shows the target's neighbourhood, not the whole case.",
    hi: "एक व्यक्ति चुनें, फिर हॉप-दर-हॉप नेटवर्क बढ़ाएँ। ग्राफ पूरे प्रकरण के बजाय लक्ष्य के पड़ोस को दिखाता है।",
    te: "ఒక వ్యక్తిని ఎంచుకుని నెట్‌వర్క్‌ను విస్తరించండి. గ్రాఫ్ మొత్తం కేసు కాకుండా లక్ష్యం యొక్క పరిసరాలను చూపుతుంది.",
    ta: "ஒரு நபரைத் தேர்ந்தெடுத்து பிணையத்தை விரிக்கவும். வரைபடம் குறிப்பிட்ட நபரின் தொடர்புகளை மட்டுமே காட்டுகிறது.",
  },
  "graph.targets": {
    en: "Persons",
    hi: "व्यक्ति",
    te: "వ్యక్తులు",
    ta: "நபர்கள்",
  },
  "graph.noPersons": {
    en: "No persons in this case graph yet — run the investigation stages first.",
    hi: "इस प्रकरण ग्राफ़ में अभी कोई व्यक्ति नहीं है — पहले जाँच चरण चलाएँ।",
    te: "ఈ కేసులో ఇంకా వ్యక్తులు లేరు — మొదట దర్యాప్తు దశలను అమలు చేయండి.",
    ta: "இன்னும் நபர்கள் இல்லை — முதலில் விசாரணை நிலைகளை இயக்கவும்.",
  },
  "graph.connections": {
    en: "connections",
    hi: "संबंध",
    te: "కనెక్షన్లు",
    ta: "தொடர்புகள்",
  },
  "graph.aka": {
    en: "aka",
    hi: "उर्फ़",
    te: "మరో పేరు",
    ta: "என்கிற",
  },
  "graph.target": {
    en: "Target",
    hi: "लक्ष्य",
    te: "లక్ష్యం",
    ta: "இலக்கு",
  },
  "graph.targetBadge": {
    en: "TARGET",
    hi: "लक्ष्य",
    te: "లక్ష్యం",
    ta: "இலக்கு",
  },
  "graph.depth": {
    en: "Neighbourhood depth",
    hi: "पड़ोस की गहराई",
    te: "నెట్‌వర్క్ లోతు (హోప్స్)",
    ta: "பிணைய ஆழம்",
  },
  "graph.truncated": {
    en: "Truncated — narrow the depth",
    hi: "सीमित — गहराई घटाएँ",
    te: "కత్తిరించబడింది — లోతును తగ్గించండి",
    ta: "சுருக்கப்பட்டது — ஆழத்தை குறைக்கவும்",
  },
  "graph.pickTarget": {
    en: "Select a person on the left to build their network.",
    hi: "नेटवर्क बनाने के लिए बाईं ओर एक व्यक्ति चुनें।",
    te: "నెట్‌వర్క్ నిర్మించడానికి ఎడమవైపు వ్యక్తిని ఎంచుకోండి.",
    ta: "பிணையத்தை உருவாக்க இடதுபுறத்தில் ஒரு நபரைத் தேர்ந்தெடுக்கவும்.",
  },
  "graph.confidence": {
    en: "confidence",
    hi: "विश्वास",
    te: "విశ్వసనీయత",
    ta: "நம்பகத்தன்மை",
  },
  "graph.setFocus": {
    en: "Set as investigation target",
    hi: "जाँच लक्ष्य बनाएँ",
    te: "దర్యాప్తు లక్ష్యంగా సెట్ చేయండి",
    ta: "விசாரணை இலக்காக அமை",
  },
  "graph.findingsAbout": {
    en: "Findings about this person",
    hi: "इस व्यक्ति पर निष्कर्ष",
    te: "ఈ వ్యక్తికి సంబంధించిన పరిశోధనలు",
    ta: "இந்த நபர் பற்றிய கண்டுபிடிப்புகள்",
  },
  "graph.relation": {
    en: "Relation",
    hi: "संबंध",
    te: "సంబంధం",
    ta: "உறவு",
  },
  "graph.selectHint": {
    en: "Select a node or an edge for its details and evidence.",
    hi: "विवरण और साक्ष्य के लिए कोई नोड या किनारा चुनें।",
    te: "వివరాలు మరియు సాక్ష్యాల కోసం నోడ్ లేదా లింక్‌ను ఎంచుకోండి.",
    ta: "விவரங்கள் மற்றும் ஆதாரங்களுக்கு முனையம் அல்லது இணைப்பைத் தேர்ந்தெடுக்கவும்.",
  },
  "graph.backend": {
    en: "Graph store",
    hi: "ग्राफ स्टोर",
    te: "గ్రాఫ్ స్టోర్",
    ta: "வரைபட களஞ்சியம்",
  },
  "graph.topology": {
    en: "Network Visualizer",
    hi: "नेटवर्क दृश्यकर्ता",
    te: "నెట్‌వర్క్ విజువలైజర్",
    ta: "பிணைய காட்சிப்படுத்தி",
  },
  "graph.liveTopology": {
    en: "Live Topology",
    hi: "सक्रिय टोपोलॉजी",
    te: "లైవ్ టోపోలాజీ",
    ta: "நேரலை பிணையம்",
  },

  // Entities
  "entity.PERSON": {
    en: "Person",
    hi: "व्यक्ति",
    te: "వ్యక్తి",
    ta: "நபர்",
  },
  "entity.PHONE": {
    en: "Phone",
    hi: "फ़ोन",
    te: "ఫోన్",
    ta: "தொலைபேசி",
  },
  "entity.BANK_ACCOUNT": {
    en: "Bank account",
    hi: "बैंक खाता",
    te: "బ్యాంక్ ఖాతా",
    ta: "வங்கி கணக்கு",
  },
  "entity.VEHICLE": {
    en: "Vehicle",
    hi: "वाहन",
    te: "వాహనం",
    ta: "வாகனம்",
  },
  "entity.LOCATION": {
    en: "Location",
    hi: "स्थान",
    te: "స్థలం",
    ta: "இருப்பிடம்",
  },
  "entity.ORGANIZATION": {
    en: "Organization",
    hi: "संगठन",
    te: "సంస్థ",
    ta: "நிறுவனம் / அமைப்பு",
  },
  "entity.EVENT": {
    en: "Event",
    hi: "घटना",
    te: "సంఘటన",
    ta: "நிகழ்வு",
  },
  "entity.CASE": {
    en: "Case",
    hi: "प्रकरण",
    te: "కేసు",
    ta: "வழக்கு",
  },

  // Investigation Workspace & Stages
  "investigation.title": {
    en: "Investigation workspace",
    hi: "जाँच कार्यक्षेत्र",
    te: "దర్యాప్తు వర్క్‌స్పేస్",
    ta: "விசாரணை பணியிடம்",
  },
  "investigation.workspaceLink": {
    en: "Investigation",
    hi: "जाँच",
    te: "దర్యాప్తు",
    ta: "விசாரணை",
  },
  "investigation.subtitle": {
    en: "Eight explicit stages. Each runs a real backend operation; a stage stays locked until the one it depends on has completed.",
    hi: "आठ स्पष्ट चरण। प्रत्येक एक वास्तविक बैकएंड ऑपरेशन चलाता है; कोई चरण तब तक बंद रहता है जब तक उसका आधार पूरा न हो।",
    te: "ఎనిమిది ఖచ్చితమైన దశలు. ప్రతి ఒక్కటి నిజమైన బ్యాకెండ్ ఆపరేషన్‌ను నడుపుతుంది; ఆధారిత దశ పూర్తయ్యే వరకు తదుపరి దశ లాక్ చేయబడుతుంది.",
    ta: "எட்டு வெளிப்படையான நிலைகள். முந்தைய நிலை முடியும் வரை அடுத்த நிலை பூட்டப்பட்டிருக்கும்.",
  },
  "investigation.openGraph": {
    en: "Open person graph",
    hi: "व्यक्ति ग्राफ़ खोलें",
    te: "వ్యక్తి గ్రాఫ్ తెరవండి",
    ta: "நபர் வரைபடத்தை திற",
  },
  "investigation.stages": {
    en: "Workflow stages",
    hi: "कार्यप्रवाह चरण",
    te: "వర్క్‌ఫ్లో దశలు",
    ta: "பணிப்பாய்வு நிலைகள்",
  },
  "investigation.run": {
    en: "Run",
    hi: "चलाएँ",
    te: "రన్ చేయండి",
    ta: "இயக்கு",
  },
  "investigation.rerun": {
    en: "Re-run",
    hi: "फिर चलाएँ",
    te: "మళ్ళీ రన్ చేయండి",
    ta: "மீண்டும் இயக்கு",
  },
  "investigation.findings": {
    en: "Findings",
    hi: "निष्कर्ष",
    te: "పరిశోధనలు",
    ta: "கண்டுபிடிப்புகள்",
  },
  "investigation.pendingDocs": {
    en: "documents still pending processing",
    hi: "दस्तावेज़ अभी लंबित हैं",
    te: "పత్రాలు ఇంకా పెండింగ్‌లో ఉన్నాయి",
    ta: "ஆவணங்கள் இன்னும் நிலுவையில் உள்ளன",
  },
  "investigation.noFindings": {
    en: "No findings yet — findings are generated by the findings stage and every one is evidence-linked.",
    hi: "अभी कोई निष्कर्ष नहीं — निष्कर्ष निष्कर्ष-चरण से बनते हैं और हर एक साक्ष्य-सहित होता है।",
    te: "ఇంకా పరిశోధనలు లేవు — పరిశోధనల దశ ద్వారా ఇవి ఉత్పత్తి చేయబడతాయి మరియు ప్రతి ఒక్కటి సాక్ష్యంతో అనుసంధానించబడి ఉంటుంది.",
    ta: "கண்டுபிடிப்புகள் எதுவும் இல்லை — ஒவ்வொரு முடிவும் ஆதாரத்துடன் இணைக்கப்பட்டுள்ளது.",
  },
  "investigation.confirm": {
    en: "Confirm",
    hi: "पुष्टि करें",
    te: "ధృవీకరించండి",
    ta: "உறுதிப்படுத்து",
  },
  "investigation.dismiss": {
    en: "Dismiss",
    hi: "खारिज करें",
    te: "రద్దు చేయండి",
    ta: "நிராகரி",
  },
  "investigation.entities": {
    en: "entities",
    hi: "संस्थाएँ",
    te: "ఎంటిటీలు",
    ta: "நிறுவனங்கள்",
  },
  "investigation.evidence": {
    en: "Evidence",
    hi: "साक्ष्य",
    te: "సాక్ష్యం",
    ta: "சான்றுகள்",
  },
  "graph.promote": {
    en: "Promote to graph",
    hi: "ग्राफ़ में जोड़ें",
    te: "గ్రాఫ్‌కు చేర్చండి",
    ta: "வரைபடத்தில் சேர்",
  },
  "graph.legend": {
    en: "Solid = confirmed relationship. Dashed = low-confidence candidate, not yet in the graph.",
    hi: "ठोस = पुष्ट संबंध। डैश = कम-विश्वास उम्मीदवार, अभी ग्राफ़ में नहीं।",
    te: "ఘన గీత = ధృవీకరించబడిన సంబంధం. చుక్కల గీత = తక్కువ నమ్మకం గల అభ్యర్థి.",
    ta: "திட வரி = உறுதிப்படுத்தப்பட்ட உறவு. கோடு = குறைந்த நம்பிக்கை கொண்ட பதிவு.",
  },

  // Review Queue (HITL)
  "review.title": {
    en: "Review queue",
    hi: "समीक्षा सूची",
    te: "సమీక్ష జాబితా",
    ta: "மதிப்பாய்வு வரிசை",
  },
  "review.identity": {
    en: "Identity matches",
    hi: "पहचान मिलान",
    te: "గుర్తింపు సరిపోలికలు",
    ta: "அடையாள பொருத்தங்கள்",
  },
  "review.patterns": {
    en: "Pattern findings",
    hi: "पैटर्न निष्कर्ष",
    te: "నమూనా పరిశోధనలు",
    ta: "முறை கண்டுபிடிப்புகள்",
  },
  "review.merge": {
    en: "Merge",
    hi: "मिलाएँ",
    te: "విలీనం చేయండి",
    ta: "இணைக்கவும்",
  },
  "review.reject": {
    en: "Reject",
    hi: "अस्वीकार करें",
    te: "తిరస్కరించండి",
    ta: "நிராகரி",
  },
  "review.unmerge": {
    en: "Unmerge",
    hi: "अलग करें",
    te: "వేరు చేయండి",
    ta: "பிரித்தெடு",
  },
  "review.note": {
    en: "Reason (required)",
    hi: "कारण (आवश्यक)",
    te: "కారణం (తప్పనిసరి)",
    ta: "காரணம் (தேவை)",
  },
  "review.noteHint": {
    en: "A decision without a written reason is rejected by the API. This is deliberate.",
    hi: "बिना लिखित कारण का निर्णय एपीआई अस्वीकार कर देता है। यह जानबूझकर है।",
    te: "లిఖితపూర్వక కారణం లేని నిర్ణయాన్ని API తిరస్కరిస్తుంది.",
    ta: "எழுதப்பட்ட காரணம் இல்லாத முடிவை API நிராகரிக்கும்.",
  },
  "review.confirm": {
    en: "Confirm finding",
    hi: "निष्कर्ष की पुष्टि",
    te: "పరిశోధనను ధృవీకరించండి",
    ta: "முடிவை உறுதிப்படுத்து",
  },
  "review.dismiss": {
    en: "Dismiss finding",
    hi: "निष्कर्ष खारिज",
    te: "పరిశోధనను రద్దు చేయండి",
    ta: "முடிவை நிராகரி",
  },
  "review.sla": {
    en: "SLA",
    hi: "एसएलए",
    te: "SLA",
    ta: "SLA",
  },
  "review.breached": {
    en: "Breached",
    hi: "उल्लंघन",
    te: "ఉల్లంఘించబడింది",
    ta: "மீறப்பட்டது",
  },
  "review.hitl": {
    en: "Human-in-the-Loop",
    hi: "मानव समीक्षा (HITL)",
    te: "హ్యూమన్-ఇన్-ది-లూప్",
    ta: "மனித மதிப்பாய்வு",
  },
  "review.actionReq": {
    en: "Action Req.",
    hi: "कार्रवाई आवश्यक",
    te: "చర్య అవసరం",
    ta: "நடவடிக்கை தேவை",
  },

  // Administration
  "admin.title": {
    en: "Administration",
    hi: "प्रशासन",
    te: "పరిపాలన",
    ta: "நிர்வாகம்",
  },
  "admin.audit": {
    en: "Audit trail",
    hi: "ऑडिट ट्रेल",
    te: "ఆడిట్ లాగ్",
    ta: "தணிக்கை பாதை",
  },
  "admin.verify": {
    en: "Verify chain",
    hi: "शृंखला सत्यापित करें",
    te: "గొలుసును ధృవీకరించండి",
    ta: "சங்கிலியை சரிபார்க்கவும்",
  },
  "admin.users": {
    en: "Users",
    hi: "उपयोगकर्ता",
    te: "వినియోగదారులు",
    ta: "பயனர்கள்",
  },
  "admin.thresholds": {
    en: "Detection thresholds",
    hi: "पहचान सीमाएँ",
    te: "గుర్తింపు పరిమితులు",
    ta: "கண்டறிதல் வரம்புகள்",
  },
  "admin.quarantine": {
    en: "Quarantine",
    hi: "संगरोध",
    te: "క్వారంటైన్",
    ta: "தனிமைப்படுத்தல்",
  },
  "admin.dataset": {
    en: "Dataset",
    hi: "डेटासेट",
    te: "డేటాసెట్",
    ta: "தரவுத்தொகுப்பு",
  },
  "admin.overview": {
    en: "Overview",
    hi: "अवलोकन",
    te: "అవలోకనం",
    ta: "கண்ணோட்டம்",
  },
  "admin.database": {
    en: "Database",
    hi: "डेटाबेस",
    te: "డేటాబేస్",
    ta: "தரவுத்தளம்",
  },
  "admin.cases": {
    en: "Cases",
    hi: "प्रकरण",
    te: "కేసులు",
    ta: "வழக்குகள்",
  },
  "admin.documents": {
    en: "Documents",
    hi: "दस्तावेज़",
    te: "పత్రాలు",
    ta: "ஆவணங்கள்",
  },
  "admin.entities": {
    en: "Entities",
    hi: "संस्थाएँ",
    te: "ఎంటిటీలు",
    ta: "நிறுவனங்கள்",
  },
  "admin.relationships": {
    en: "Relationships",
    hi: "संबंध",
    te: "సంబంధాలు",
    ta: "உறவுகள்",
  },
  "admin.ai": {
    en: "AI Activity",
    hi: "एआई गतिविधि",
    te: "ఏఐ కార్యాచరణ",
    ta: "AI செயல்பாடு",
  },
  "admin.health": {
    en: "System Health",
    hi: "सिस्टम स्वास्थ्य",
    te: "సిస్టమ్ ఆరోగ్యం",
    ta: "கணினி நிலை",
  },

  // System states
  "state.loading": {
    en: "Loading…",
    hi: "लोड हो रहा है…",
    te: "లోడ్ అవుతోంది…",
    ta: "ஏற்றுகிறது…",
  },
  "state.empty": {
    en: "Nothing to show.",
    hi: "दिखाने के लिए कुछ नहीं।",
    te: "చూపించడానికి ఏమీ లేదు.",
    ta: "காட்டுவதற்கு எதுவும் இல்லை.",
  },
  "state.error": {
    en: "Something went wrong.",
    hi: "कुछ गलत हो गया।",
    te: "ఏదో తప్పు జరిగింది.",
    ta: "ஏதோ தவறு நடந்துவிட்டது.",
  },
  "state.retry": {
    en: "Retry",
    hi: "पुनः प्रयास",
    te: "మళ్ళీ ప్రయత్నించండి",
    ta: "மீண்டும் முயற்சி செய்",
  },
  "state.forbidden": {
    en: "Your role does not allow this action.",
    hi: "आपकी भूमिका इस कार्य की अनुमति नहीं देती।",
    te: "మీ పాత్ర ఈ చర్యను అనుమతించదు.",
    ta: "உங்கள் பதவிக்கு இந்த அனுமதி இல்லை.",
  },

  // Environment banner & datasets
  "env.banner": {
    en: "Development environment — corpus records are marked [SYNTHETIC] and are not operational police data.",
    hi: "विकास वातावरण — कॉर्पस रिकॉर्ड [SYNTHETIC] चिह्नित हैं और वास्तविक पुलिस डेटा नहीं हैं।",
    te: "అభివృద్ధి వాతావరణం — రికార్డులు [SYNTHETIC] గా గుర్తించబడ్డాయి మరియు నిజమైన పోలీసు డేటా కాదు.",
    ta: "உருவாக்க சூழல் — பதிவுகள் [SYNTHETIC] எனக் குறிக்கப்பட்டுள்ளன, உண்மையான காவல் தரவு அல்ல.",
  },
  "dataset.validate": {
    en: "Validate Dataset",
    hi: "डेटासेट सत्यापित करें",
    te: "డేటాసెట్‌ను ధృవీకరించండి",
    ta: "தரவுத்தொகுப்பை சரிபார்க்கவும்",
  },
  "dataset.import": {
    en: "Import Dataset",
    hi: "डेटासेट आयात करें",
    te: "డేటాసెట్‌ను దిగుమతి చేయండి",
    ta: "தரவுத்தொகுப்பை இறக்குமதி செய்",
  },
  "dataset.refresh": {
    en: "Refresh status",
    hi: "स्थिति ताज़ा करें",
    te: "స్థితిని రిఫ్రెష్ చేయండి",
    ta: "நிலையை புதுப்பி",
  },
  "dataset.idle": {
    en: "Idle — nothing is being imported.",
    hi: "निष्क्रिय — कोई आयात नहीं चल रहा।",
    te: "నిష్క్రియంగా ఉంది — ఏమీ దిగుమతి కావడం లేదు.",
    ta: "செயலற்றது — எதுவும் இறக்குமதி செய்யப்படவில்லை.",
  },

  // Entity & Relationship Tables & Pager
  "entities.name": {
    en: "Name",
    hi: "नाम",
    te: "పేరు",
    ta: "பெயர்",
  },
  "entities.searchPlaceholder": {
    en: "Search by name…",
    hi: "नाम से खोजें…",
    te: "పేరు ద్వారా వెతకండి…",
    ta: "பெயரால் தேடுக…",
  },
  "entities.allTypes": {
    en: "All types",
    hi: "सभी प्रकार",
    te: "అన్ని రకాలు",
    ta: "அனைத்து வகைகள்",
  },
  "rel.source": {
    en: "Source",
    hi: "स्रोत",
    te: "మూలం",
    ta: "மூலம்",
  },
  "pager.previous": {
    en: "Previous",
    hi: "पिछला",
    te: "మునుపటి",
    ta: "முந்தைய",
  },
  "pager.next": {
    en: "Next",
    hi: "अगला",
    te: "తరువాతి",
    ta: "அடுத்தது",
  },
  "pager.of": {
    en: "of",
    hi: "का",
    te: "మొత్తం లో",
    ta: "இல்",
  },
  "sources.size": {
    en: "Size",
    hi: "आकार",
    te: "పరిమాణం",
    ta: "அளவு",
  },
  "sources.reload": {
    en: "Reload",
    hi: "पुनः लोड करें",
    te: "రీలోడ్ చేయండి",
    ta: "மீண்டும் ஏற்று",
  },

  // Evidentiary Footer
  "footer.sessionVerified": {
    en: "Session Verified",
    hi: "सत्र सत्यापित",
    te: "సెషన్ ధృవీకరించబడింది",
    ta: "அமர்வு சரிபார்க்கப்பட்டது",
  },
  "footer.platformTitle": {
    en: "CrimeLink Intelligence Platform",
    hi: "क्राइमलिंक इंटेलिजेंस प्लेटफॉर्म",
    te: "క్రైమ్‌లింక్ ఇంటెలిజెన్స్ ప్లాట్‌ఫారమ్",
    ta: "கிரைம்லிங்க் புலனாய்வு தளம்",
  },
  "footer.evidenceBacked": {
    en: "Evidence-backed investigation",
    hi: "साक्ष्य-आधारित जाँच",
    te: "ఆధారాలతో కూడిన దర్యాప్తు",
    ta: "சான்றுகள் அடிப்படையிலான விசாரணை",
  },
  "footer.auditLog": {
    en: "Tamper-evident SHA-256 audit log",
    hi: "छेड़छाड़-साक्ष्य SHA-256 ऑडिट लॉग",
    te: "ట్యాంపర్-స్పష్టమైన SHA-256 ఆడిట్ లాగ్",
    ta: "சிதைக்க முடியாத SHA-256 தணிக்கை பதிவு",
  },
  "footer.authorizedOnly": {
    en: "Authorized Law Enforcement Access Only",
    hi: "केवल अधिकृत कानून प्रवर्तन पहुँच",
    te: "అధీకృత చట్ట అమలు అధికారులకు మాత్రమే",
    ta: "அங்கீகரிக்கப்பட்ட அதிகாரிகளுக்கு மட்டுமே",
  },
};

const STORAGE_KEY = "crimelink.lang";

export function currentLang(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY) as Lang;
  if (stored === "en" || stored === "hi" || stored === "te" || stored === "ta") {
    return stored;
  }
  return "en";
}

export function setLang(lang: Lang) {
  localStorage.setItem(STORAGE_KEY, lang);
  window.location.reload();
}

export function t(key: string, lang: Lang = currentLang()): string {
  const entry = STRINGS[key];
  if (!entry) return key;
  return entry[lang] || entry["en"] || key;
}
