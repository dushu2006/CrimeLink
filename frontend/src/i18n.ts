/**
 * Bilingual labels (PRD 14.2: every field label carries its Hindi counterpart).
 *
 * The strings live in one object so a translation pass has a single place to
 * visit, and so a missing Hindi string is a type error rather than a blank
 * label in a district that works in Hindi.
 */

export type Lang = "en" | "hi";

export const STRINGS: Record<string, [string, string]> = {
  "app.title": ["CrimeLink", "क्राइमलिंक"],
  "app.subtitle": [
    "Criminal Network Analysis — Ministry of Home Affairs / NCRB",
    "आपराधिक नेटवर्क विश्लेषण — गृह मंत्रालय / एनसीआरबी",
  ],

  "nav.cases": ["Cases", "प्रकरण"],
  "nav.graph": ["Network graph", "नेटवर्क ग्राफ"],
  "nav.review": ["Review queue", "समीक्षा सूची"],
  "nav.admin": ["Administration", "प्रशासन"],
  "nav.signout": ["Sign out", "साइन आउट"],

  "login.badge": ["Badge number", "बैज नंबर"],
  "login.password": ["Password", "पासवर्ड"],
  "login.submit": ["Sign in", "साइन इन करें"],
  "login.heading": ["Sign in to CrimeLink", "क्राइमलिंक में साइन इन करें"],
  "login.note": [
    "Every action is recorded in a tamper-evident audit log.",
    "हर कार्य को छेड़छाड़-साक्ष्य ऑडिट लॉग में दर्ज किया जाता है।",
  ],
  "setup.heading": ["Create the first administrator", "प्रथम प्रशासक बनाएँ"],
  "setup.submit": ["Create administrator", "प्रशासक बनाएँ"],
  "setup.note": [
    "This form appears only while the system has no users. The account you create is an ADMIN.",
    "यह फ़ॉर्म केवल तब दिखता है जब सिस्टम में कोई उपयोगकर्ता नहीं है। बनाया गया खाता ADMIN होगा।",
  ],
  "setup.fullName": ["Full name", "पूरा नाम"],
  "setup.station": ["Station ID", "थाना आईडी"],
  "setup.jurisdiction": ["Jurisdiction ID", "क्षेत्राधिकार आईडी"],
  "setup.jurisdictionHint": [
    "Use SYN-DEV so imported synthetic cases are visible in this account.",
    "SYN-DEV का उपयोग करें ताकि आयातित सिंथेटिक प्रकरण दिखें।",
  ],
  "admin.createUser": ["Create user", "उपयोगकर्ता बनाएँ"],
  "admin.password": ["Password", "पासवर्ड"],
  "admin.role": ["Role", "भूमिका"],
  "admin.station": ["Station", "थाना"],

  "cases.title": ["Cases", "प्रकरण"],
  "cases.new": ["Register case", "नया प्रकरण"],
  "cases.number": ["Case number", "प्रकरण संख्या"],
  "cases.name": ["Title", "शीर्षक"],
  "cases.jurisdiction": ["Jurisdiction", "क्षेत्राधिकार"],
  "cases.documents": ["Documents", "दस्तावेज़"],
  "cases.pending": ["Pending review", "लंबित समीक्षा"],
  "cases.status": ["Status", "स्थिति"],
  "cases.empty": [
    "No cases in your jurisdiction yet. Imported synthetic cases use jurisdiction SYN-DEV. An administrator can import the dataset from Administration → Dataset.",
    "आपके क्षेत्राधिकार में अभी कोई प्रकरण नहीं है। आयातित सिंथेटिक प्रकरण SYN-DEV क्षेत्राधिकार में हैं। प्रशासक Administration → Dataset से आयात कर सकते हैं।",
  ],

  "case.detail": ["Case", "प्रकरण"],
  "case.upload": ["Upload document", "दस्तावेज़ अपलोड करें"],
  "case.documents": ["Documents", "दस्तावेज़"],
  "case.timeline": ["Timeline", "समय-रेखा"],
  "case.export": ["Export PDF brief", "पीडीएफ़ ब्रीफ़ निर्यात करें"],
  "case.processing": ["Processing", "प्रसंस्करण"],
  "case.openGraph": ["Open network graph", "नेटवर्क ग्राफ खोलें"],
  "case.review": ["Review queue", "समीक्षा सूची"],

  "doc.type": ["Type", "प्रकार"],
  "doc.file": ["File", "फ़ाइल"],
  "doc.language": ["Language", "भाषा"],
  "doc.confidence": ["Source confidence", "स्रोत विश्वास"],
  "doc.status": ["Status", "स्थिति"],
  "doc.hash": ["SHA-256", "SHA-256"],
  "doc.quarantined": ["Quarantined", "संगरोधित"],

  "graph.title": ["Network graph", "नेटवर्क ग्राफ"],
  "graph.influence": ["Influence", "प्रभाव"],
  "graph.evidence": ["Evidence", "साक्ष्य"],
  "graph.explanation": ["Why this score?", "यह स्कोर क्यों?"],
  "graph.expand": ["Expand", "विस्तार करें"],
  "graph.paths": ["Temporal path search", "समय-आधारित पथ खोज"],
  "graph.staging": ["Low-confidence candidates", "कम-विश्वास उम्मीदवार"],
  "graph.promote": ["Promote to graph", "ग्राफ़ में जोड़ें"],
  "graph.legend": [
    "Solid = confirmed relationship. Dashed = low-confidence candidate, not yet in the graph.",
    "ठोस = पुष्ट संबंध। डैश = कम-विश्वास उम्मीदवार, अभी ग्राफ़ में नहीं।",
  ],

  "review.title": ["Review queue", "समीक्षा सूची"],
  "review.identity": ["Identity matches", "पहचान मिलान"],
  "review.patterns": ["Pattern findings", "पैटर्न निष्कर्ष"],
  "review.merge": ["Merge", "मिलाएँ"],
  "review.reject": ["Reject", "अस्वीकार करें"],
  "review.unmerge": ["Unmerge", "अलग करें"],
  "review.note": ["Reason (required)", "कारण (आवश्यक)"],
  "review.noteHint": [
    "A decision without a written reason is rejected by the API. This is deliberate.",
    "बिना लिखित कारण का निर्णय एपीआई अस्वीकार कर देता है। यह जानबूझकर है।",
  ],
  "review.confirm": ["Confirm finding", "निष्कर्ष की पुष्टि"],
  "review.dismiss": ["Dismiss finding", "निष्कर्ष खारिज"],
  "review.sla": ["SLA", "एसएलए"],
  "review.breached": ["Breached", "उल्लंघन"],

  "admin.title": ["Administration", "प्रशासन"],
  "admin.audit": ["Audit trail", "ऑडिट ट्रेल"],
  "admin.verify": ["Verify chain", "शृंखला सत्यापित करें"],
  "admin.users": ["Users", "उपयोगकर्ता"],
  "admin.thresholds": ["Detection thresholds", "पहचान सीमाएँ"],
  "admin.quarantine": ["Quarantine", "संगरोध"],
  "admin.dataset": ["Dataset", "डेटासेट"],
  "admin.overview": ["Overview", "अवलोकन"],
  "admin.database": ["Database", "डेटाबेस"],
  "admin.cases": ["Cases", "प्रकरण"],
  "admin.documents": ["Documents", "दस्तावेज़"],
  "admin.entities": ["Entities", "संस्थाएँ"],
  "admin.relationships": ["Relationships", "संबंध"],
  "admin.ai": ["AI Activity", "एआई गतिविधि"],
  "admin.health": ["System Health", "सिस्टम स्वास्थ्य"],

  "state.loading": ["Loading…", "लोड हो रहा है…"],
  "state.empty": ["Nothing to show.", "दिखाने के लिए कुछ नहीं।"],
  "state.error": ["Something went wrong.", "कुछ गलत हो गया।"],
  "state.retry": ["Retry", "पुनः प्रयास"],
  "state.forbidden": [
    "Your role does not allow this action.",
    "आपकी भूमिका इस कार्य की अनुमति नहीं देती।",
  ],

  "env.banner": [
    "Development environment — corpus records are marked [SYNTHETIC] and are not operational police data.",
    "विकास वातावरण — कॉर्पस रिकॉर्ड [SYNTHETIC] चिह्नित हैं और वास्तविक पुलिस डेटा नहीं हैं।",
  ],
  "dataset.validate": ["Validate Dataset", "डेटासेट सत्यापित करें"],
  "dataset.import": ["Import Dataset", "डेटासेट आयात करें"],
  "dataset.refresh": ["Refresh status", "स्थिति ताज़ा करें"],
  "dataset.idle": ["Idle — nothing is being imported.", "निष्क्रिय — कोई आयात नहीं चल रहा।"],
};

const STORAGE_KEY = "crimelink.lang";

export function currentLang(): Lang {
  return (localStorage.getItem(STORAGE_KEY) as Lang) || "en";
}

export function setLang(lang: Lang) {
  localStorage.setItem(STORAGE_KEY, lang);
  window.location.reload();
}

export function t(key: string, lang: Lang = currentLang()): string {
  const pair = STRINGS[key];
  if (!pair) return key;
  return lang === "hi" ? pair[1] : pair[0];
}
