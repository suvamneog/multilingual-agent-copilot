/* Production-RAG acceptance cases (rag.md.txt, section 6), run in the real chat page.
   Open /story-test.html?cases=rag-cases.js#run against the server on port 8001.

   A step types text ({ say }) or clicks a button ({ click }). After the step, every `expect`
   regex must match the bot's new messages and no `not` regex may. `count: [selector, n]`
   checks the number of elements in the whole chat (used for "Close the sale" twice).
   {D36} is replaced by the date 36 months from today. */

const NO_ERR = /Couldn't reach|Something went wrong|\bError\b/;
const PAGE = /PDF page \d+/;
const DATE = /\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|December) 20\d\d\b/;
const PREMIUM = /Premium is not written in this wording\. Money is not compared\. Premium comes from the insurer rate card after the health check\.|Premium policy wording mein nahi likha hota\. Paisa compare nahi kiya\./;

window.STORY_CASES = [
  /* ── the seven need cases ─────────────────────────────────── */
  { f: 3, name: "1 Cancer: father 58, treated 2 years ago", wait: 60000, steps: [
    { say: "My father is 58. He had cancer treatment 2 years ago, now he is fine. Will cancer be covered?",
      expect: [/Best/, DATE, PAGE, /Cancer treated before counts as a pre-existing disease/, /Why not the others/i],
      not: [NO_ERR, /\bThird\b/] }] },
  { f: 3, name: "2 Diabetes: mother 55, exact long Hinglish line", wait: 60000, steps: [
    { say: "meri ma 55 years ki hai. unhe diabetes hai. unke ghutne meh bohot jyada dard rehta hai. humne pehle insurance pehle kuch try kiya tha. but unme pre existing disease ka jo waiting period hota hai, woh 3 saal se jyada ka hota hai. esa kuch hai kya jisse mummy ko hum ek health insurance plan meh dal sake. Esa koi health insurance plan hai kya, jaha peh mummy ko dal sake aur pese bhi kam lage.",
      expect: [/Option 1/, /Option 2/, DATE, PAGE, /Paisa compare nahi kiya/, /Ghutne ka dard/, /Baaki kyun nahi/i],
      not: [NO_ERR, /Sabse behtar|Best:/] }] },
  { f: 3, name: "3 Heart: mother 62, bypass 3 years ago, BP medicine", wait: 60000, steps: [
    { say: "My mother is 62. She had a bypass 3 years ago and takes BP medicine daily.",
      expect: [/Best/, DATE, PAGE, /not from day 1/], not: [NO_ERR, /covered from day 1/i] }] },
  { f: 3, name: "4 Senior: father 68, healthy, no diabetes, no BP, no disease", wait: 60000, steps: [
    { say: "My father is 68, healthy, no diabetes, no BP, no disease.",
      expect: [/Not counted \(you said there is none\): diabetes, high BP/, /no pre-existing wait applies/, /Best/],
      not: [NO_ERR, /36 months|Diabetes cover|pre-existing wait \d+ months/i] }] },
  { f: 3, name: "5 Maternity: wife 28, baby next year", wait: 60000, steps: [
    { say: "My wife is 28. We are planning a baby next year.",
      expect: [/maternity wait \d+ months/, DATE, PAGE, /No plan covers it in time/, /Why not the others/i],
      not: [NO_ERR] }] },
  { f: 3, name: "6 Young healthy, 30, cheap, low premium", wait: 60000, steps: [
    { say: "I am 30, no disease, I want a cheap plan with low premium.",
      expect: [/Option 1/, /Money was not compared/, /no pre-existing wait applies/], not: [NO_ERR, /Best:|Best fit/] }] },
  { f: 3, name: "7 Knee replacement: mother 60, surgery in 6 months", wait: 60000, steps: [
    { say: "My mother is 60 and needs a knee replacement surgery in 6 months.",
      expect: [/joint replacement wait \d+ months/, DATE, PAGE, /No plan covers it in time/], not: [NO_ERR] }] },

  /* ── the mother story, one continuous chat, no reload ─────── */
  { f: 5, name: "Mother story end to end (one chat)", wait: 120000, steps: [
    { say: "meri ma 55 years ki hai. unhe diabetes hai. unke ghutne meh bohot jyada dard rehta hai. humne pehle insurance pehle kuch try kiya tha. but unme pre existing disease ka jo waiting period hota hai, woh 3 saal se jyada ka hota hai. esa kuch hai kya jisse mummy ko hum ek health insurance plan meh dal sake. Esa koi health insurance plan hai kya, jaha peh mummy ko dal sake aur pese bhi kam lage.",
      expect: [/Option 1/, DATE, PAGE] },
    { say: "Optima", expect: [/Which exact product/, /Optima Secure V08/, /only after you pick/] },
    { click: /Optima Secure V08/, expect: [/Now answering only about HDFC ERGO Optima Secure V08/] },
    { say: "premium kitna hoga", expect: [PREMIUM, /₹34,569 saal ka/, /diabetes ya bimari ki loading se pehle/] },
    { say: "cataract limit kya hai", expect: [/page 31/i, /24 months/, /In simple words/, /Specified Disease|Code-Excl02/i], not: [/For which product/] },
    { say: "robotic surgery covered hai?", expect: [/not written|Not written/], not: [NO_ERR, /For which product/] },
    { say: "AYUSH covered hai?", expect: [/page 12/i, /AYUSH/, /1\.4/], not: [/For which product/] },
    /* the chat is in Hinglish, so the kit is too */
    { say: "Close the sale", expect: [/(?:first 30 days|pehle 30 din ke baad)[^)]*\((?:PDF )?page 32\)/i, /Waiting ends|Waiting khatam/i,
      /Do not promise|Yeh promise mat karo/i, /UIN/],
      not: [/covers new illnesses[^.]*once the policy starts|policy start hote hi/], count: ["[data-kit]", 1] },
    { say: "Close the sale", count: ["[data-kit]", 1] },
    { say: "National Mediclaim", expect: [/stopped for new sales on 1 Apr 2024/, /Renewals only/] },
  ] },

  /* ── the dangerous bugs (section 3) ───────────────────────── */
  { f: 2, name: "Negation: 'diabetes nahi hai' is absent", wait: 60000, steps: [
    { say: "papa 66 saal ke hain, diabetes nahi hai, BP nahi hai", expect: [/Count nahi kiya|Not counted/, /PED wait nahi|no pre-existing wait/i],
      not: [/Diabetes cover|36 months/i] }] },
  { f: 4, name: "Heart surgery: no 'covered from day 1', no Home Care / piles citation", wait: 120000, steps: [
    { say: "Optima Secure V08", expect: [/Now answering only about/] },
    { say: "Is heart surgery covered from day 1?", not: [NO_ERR, /covered from day 1/i, /Home Care/i, /piles|hernia/i, /For which product/,
      /48 months/] }] },  /* V08's PED wait is 36; 48 is the PED definition's look-back */
  { f: 4, name: "Waiting list is not an exclusion: knee replacement", wait: 120000, steps: [
    { say: "Optima Secure V08", expect: [/Now answering only about/] },
    { say: "Is knee replacement covered?", expect: [/waiting list/, /24 months/, /page 32/i],
      not: [NO_ERR, /\bnot covered\b/i, /\bis (?:a )?permanent(?:ly)? exclu/i, /For which product/] }] },
  { f: 4, name: "Hard question answers on first ask after a pick", wait: 120000, steps: [
    { say: "My mother is 55 with diabetes", expect: [/Best|Option 1/] },
    { click: /^Pick this$/, expect: [/Now answering only about/] },
    { say: "Is AYUSH covered?", expect: [/AYUSH/], not: [/For which product/, /Which exact product/] }] },
  { f: 3, name: "'Also check (not ranked)' is stable between turns", wait: 60000, steps: [
    { say: "My father is 58 with BP", expect: [/Best/] },
    { say: "My father is 58 with BP", expect: [/Best/] }], same: true },
];
