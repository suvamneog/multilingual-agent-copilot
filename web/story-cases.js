/* Story test cases for the 5 features (quickinsure-story-test.md), plus the
   ways agents actually type. Run them at /story-test.html against the full
   server (run_demo.ps1 / run_demo.sh).

   A case is a list of steps. A step either types text ({ say }) or clicks the
   last chat button whose text matches ({ click }). After a step, `expect`
   regexes must all match the bot's new messages and `not` regexes must not.
   {D36} is replaced by the date 36 months from today (the Optima Secure V08
   pre-existing wait), {D24} by 24 months.

   Every sentence that once failed stays here so it cannot break again. */

/* ranking now comes from the wordings (rank.py, production-RAG branch). For a 55-year-old with diabetes the
   wordings put Generali Central Health Absolute first (PED 24 months, clause 3.1.1.1 PDF page 23; entry age
   "Adult – No limit", PDF page 50) and Optima Secure V08 second (36 months, PDF page 30). A low-budget need
   is shown as "Option 1 / 2", never "Best". */
const BEST_EN = /(?:Best|Option 1): Generali Central Health Absolute/;
const BEST_HL = /(?:Sabse behtar|Option 1): Generali Central Health Absolute/;
const SECOND_EN = /(?:Second|Option 2): HDFC ERGO Optima Secure V08/;
const SECOND_HL = /(?:Doosra|Option 2): HDFC ERGO Optima Secure V08/;
const ONLY_TWO = /\bThird\b|Teesra|तीसरा/;
const MONEY_EN = /Money was not compared/;
const MONEY_HL = /Paisa compare nahi kiya/;
const MONEY_HI = /पैसे की तुलना नहीं/;
const DATE = /\{D36\}/; // placeholder, replaced in the runner
const V08 = { say: "Optima Secure V08" };
const NO_ERR = /Couldn't reach|Something went wrong|\bError\b/;

window.STORY_CASES = [
  /* ── the story, exactly as written ───────────────────────── */
  { f: 3, name: "Story (English): best + second, date, money line, knee",
    steps: [{ say: "My mother is 55 years old. She has diabetes. She has severe pain in her knees. We tried insurance before, but in all of them the waiting period for a pre-existing disease was more than 3 years. Is there a health insurance plan where I can put my mother, and the premium is also low?",
      expect: [BEST_EN, SECOND_EN, "{D24}", MONEY_EN, /Shortest wait among plans where entry age is confirmed/, /Knee/, /Why not the others/i], not: [ONLY_TWO, /Best:/] }] },
  { f: 3, name: "Story (Hinglish): best + second, date, money line",
    steps: [{ say: "meri ma 55 years ki hai. unhe diabetes hai. unke ghutne meh bohot jyada dard rehta hai. humne pehle insurance pehle kuch try kiya tha. but unme pre existing disease ka jo waiting period hota hai, woh 3 saal se jyada ka hota hai. esa kuch hai kya jisse mummy ko hum ek health insurance plan meh dal sake. Esa koi health insurance plan hai kya, jaha peh mummy ko dal sake aur pese bhi kam lage.",
      expect: [BEST_HL, SECOND_HL, "{D24}", MONEY_HL, /Ghutne/, /Baaki kyun nahi/i], not: [ONLY_TWO, /Sabse behtar:/] }] },
  { f: 3, name: "Spec case: mother 50, diabetes, room rent above 10,000",
    steps: [{ say: "Mother is 50, has diabetes, wants room rent above 10,000",
      expect: [/Best/, /10,000/, /PDF page \d+/], not: [ONLY_TWO] }] },

  /* ── need-first, the way agents type it ──────────────────── */
  { f: 2, name: "EN short: mother 55 diabetic, low premium",
    steps: [{ say: "mother 55 diabetic, low premium plan?", expect: [BEST_EN, MONEY_EN], not: [ONLY_TWO] }] },
  { f: 2, name: "EN: mom has sugar, cheap policy",
    steps: [{ say: "My mom is 55, has sugar. Want cheap policy", expect: [BEST_EN, MONEY_EN] }] },
  { f: 2, name: "EN: father 60 BP, budget 15k",
    steps: [{ say: "father 60 BP patient, budget 15k", expect: [/Best|Option 1/, /budget was not checked|not compared/i] }] },
  { f: 2, name: "EN: premium should be less",
    steps: [{ say: "Plan for my mother, 55, diabetes. The premium should be less.", expect: [BEST_EN, MONEY_EN] }] },
  { f: 2, name: "EN: budget is limited",
    steps: [{ say: "mother 55 with diabetes, our budget is limited", expect: [BEST_EN, MONEY_EN] }] },
  { f: 2, name: "EN typos: mothr, yrs, diabetis",
    steps: [{ say: "my mothr is 55 yrs, diabetis, premium low", expect: [/Option 1/, MONEY_EN, /pre-existing wait \d+ months/] }] },
  { f: 2, name: "EN: papa heart problem",
    steps: [{ say: "papa 58 heart problem, need health plan", expect: [/Best/, /Heart/i] }] },
  { f: 2, name: "HL: mummy sugar, sasta plan",
    steps: [{ say: "mummy 55 saal ki hai sugar hai sasta plan batao", expect: [BEST_HL, MONEY_HL] }] },
  { f: 2, name: "HL: papa BP, premium kam",
    steps: [{ say: "papa ki umar 60 hai bp hai premium kam chahiye", expect: [/Sabse behtar|Best|Option 1/, MONEY_HL] }] },
  { f: 2, name: "HL WhatsApp style: h for hai",
    steps: [{ say: "maa 55 sal ki h diabetes h ghutno me dard h", expect: [BEST_HL, /Ghutne/] }] },
  { f: 2, name: "HL: waiting 3 saal se kam",
    steps: [{ say: "meri mummy 55 ki hai, sugar ki problem hai, 3 saal se kam waiting chahiye", expect: [/Sabse behtar|Best/, "{D24}"] }] },
  { f: 2, name: "HL: room 10000 se upar",
    steps: [{ say: "mummy ke liye plan chahiye 50 saal sugar room 10000 se upar", expect: [/Sabse behtar|Best/, /10,000/] }] },
  { f: 2, name: "HI: माँ 55, डायबिटीज़, सस्ता",
    steps: [{ say: "मेरी माँ 55 साल की हैं, उन्हें डायबिटीज़ है, सस्ता प्लान बताइए", expect: [/विकल्प 1|सबसे अच्छा|Best/, MONEY_HI] }] },
  { f: 2, name: "HI: पापा 60, बीपी",
    steps: [{ say: "पापा 60 साल के हैं, बीपी है, कौन सा प्लान लें?", expect: [/सबसे अच्छा|Best/] }] },
  { f: 2, name: "My insurers = ICICI Lombard: their products first",
    mine: ["ICICI Lombard"],
    steps: [{ say: "mother 55 diabetes", expect: [/ICICI Lombard/, /Your insurers first|None of your insurers fit/] }] },

  /* ── feature 1: which exact product ──────────────────────── */
  { f: 1, name: "Optima → 3 options with a difference, asks first",
    steps: [{ say: "Optima", expect: [/Which exact product/, /Optima Restore/, /Optima Secure V08/, /Optima Senior/, /more match/, /only after you pick/] }] },
  { f: 1, name: "Optima + question → asks first, answers after pick",
    steps: [{ say: "Optima waiting period kitna hai?", expect: [/Which exact product/] },
      { click: /Optima Secure V08/, expect: [/Now answering only about/, /36 months/] }] },
  { f: 1, name: "Niva ReAssure → asks which version",
    steps: [{ say: "niva reassure", expect: [/Which exact product|Now answering only about/] }] },
  { f: 1, name: "Star → asks which Star product",
    steps: [{ say: "star health", expect: [/Which exact product/] }] },
  { f: 2, name: "Name-first: Optima Secure V08 → only that product",
    steps: [{ ...V08, expect: [/Now answering only about HDFC ERGO Optima Secure V08/, /No other product is mixed in/] }] },
  { f: 2, name: "Name-first lower case: care supreme",
    steps: [{ say: "care supreme", expect: [/Now answering only about|Which exact product/] }] },

  /* ── feature 4: simple and hard, on Optima Secure V08 ────── */
  { f: 4, name: "EN simple questions", steps: [V08,
    { say: "How much will the premium be?", expect: [/Premium is not written in this wording/, /rate card/, /₹34,569/] },
    { say: "What is the waiting time?", expect: [/Fast answer/i, /36 months/, "{D36}"] },
    { say: "What is the room rent?", expect: [/Fast answer/i, /At Actuals/i] },
    { say: "What is the age limit?", expect: [/Fast answer/i, /No upper limit/i] }] },
  { f: 4, name: "HL simple questions", steps: [V08,
    { say: "premium kitna hoga?", expect: [/Premium policy wording mein nahi likha hota/, /₹34,569/] },
    { say: "waiting time kitna hai?", expect: [/36 months/] },
    { say: "room rent kya hai?", expect: [/At Actuals/i] },
    { say: "age limit kya hai?", expect: [/No upper limit/i] }] },
  { f: 4, name: "Agent-style simple: sugar ka waiting, kamra",
    steps: [V08,
      { say: "sugar ka waiting kitne din ka hai", expect: [/36 months/] },
      { say: "kamra kitne ka milega", expect: [/At Actuals/i] }] },
  { f: 4, name: "Hard: cataract → exact line, page 31, simple words", steps: [V08,
    { say: "What is the cataract limit?", expect: [/Exact policy line/i, /PDF page 31/i, /Code-Excl02/i, /In simple words/, /listed-illness waiting list/] }] },
  { f: 4, name: "Hard: motiyabind (Hinglish cataract)", steps: [V08,
    { say: "motiyabind ka limit kya hai", expect: [/PDF page 31/i, /In simple words/] }] },
  { f: 4, name: "Hard: AYUSH → exact line with page", steps: [V08,
    { say: "Is AYUSH covered?", expect: [/Exact policy line/i, /AYUSH/, /PDF page 12/i, /In simple words/] }] },
  { f: 4, name: "Hard: robotic surgery → wording search, cited or not written", wait: 180000, steps: [V08,
    { say: "Is robotic surgery covered?", expect: [/Checked against the policy text|Not written in this product|Outside this wording/i], not: [NO_ERR] }] },
  { f: 4, name: "Hard HL: robotic surgery covered hai?", wait: 180000, steps: [V08,
    { say: "robotic surgery covered hai?", expect: [/Checked against the policy text|Not written in this product|Outside this wording/i], not: [NO_ERR] }] },
  /* once failed: "maternity" is in the Care Joy product name, so the question switched product */
  { f: 1, name: "Topic word in a question does not switch product", wait: 180000, steps: [V08,
    { say: "Is maternity covered?", expect: [/Checked against the policy text|Not written in this product|Outside this wording/i], not: [NO_ERR, /Now answering only about.*Care Joy/] }] },
  /* once answered "Maternity expenses are covered after 2 years" from a brochure add-on page (p69),
     while the base wording excludes maternity (Code-Excl18, p34) */
  { f: 4, name: "Maternity on V08: base excludes it, add-on only", wait: 180000, steps: [V08,
    { say: "Is maternity covered?", expect: [/not covered|excluded|exclusion|not part of the base|does not cover/i, /add-on|optional/i], not: [NO_ERR] }] },
  { f: 4, name: "Maternity Hinglish on V08: base excludes it", wait: 180000, steps: [V08,
    { say: "maternity cover hai kya?", expect: [/nahi|not covered|excluded|exclusion/i], not: [NO_ERR] }] },
  { f: 1, name: "A real product name still switches product", steps: [V08,
    { say: "Care Supreme", expect: [/Now answering only about Care Health Care Supreme|Which exact product/] }] },
  { f: 1, name: "Topic word: cancer covered hai?", wait: 180000, steps: [V08,
    { say: "cancer covered hai?", not: [/Now answering only about.*Cancer Care/, NO_ERR] }] },

  /* ── feature 5: close the sale ───────────────────────────── */
  { f: 5, name: "Close kit after the English story", steps: [
    { say: "My mother is 55 years old. She has diabetes. She has severe pain in her knees. Is there a plan where the premium is also low?", expect: [BEST_EN, SECOND_EN] },
    V08,  /* the story's agent sells Optima Secure V08 (option 2); the need stays with the chat */
    { say: "Close the sale", expect: [/Say this to the customer/i, /Knee pain is already there/, "{D36}", /WhatsApp message/i, /UIN HDFHLIP26058V082526/, /Waiting ends/i, /Papers needed to buy/i, /Pre-policy medical check-up/, /Papers at claim time/i, /Do not promise/i, /Do not quote a premium/],
      not: [/Aadhaar|PAN card|X-ray/i] }] },
  { f: 5, name: "Close kit after the Hinglish story", steps: [
    { say: "maa 55 saal ki hai diabetes hai ghutne me dard hai, pese kam lage", expect: [BEST_HL, SECOND_HL] },
    V08,
    { say: "Close the sale", expect: [/Customer se yeh bolo/i, /Ghutne ka dard pehle se hai/, "{D36}", /WhatsApp/i, /Yeh promise mat karo/i] }] },
  { f: 5, name: "Stopped: National Mediclaim, no other company's product",
    steps: [{ say: "National Mediclaim", expect: [/Stopped product/i, /stopped for new sales on 1 Apr 2024/, /do not sell it to a new customer/, /No current National Insurance wording/], not: [/TATA AIG/] }] },
  { f: 5, name: "Stopped: old Optima Secure points to V08",
    steps: [{ say: "Optima Secure Suraksha", expect: [/Stopped/i, /Optima Secure V08/] }] },
];
