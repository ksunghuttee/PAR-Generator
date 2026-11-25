/* Languages of the deliverables, for the in-browser preview (the server uses par_generator/i18n.py).
   Reads the shared strings (par_generator/i18n.json) from window.PAR_I18N, embedded by
   scripts/build_preview.py. PARTr("fr")("h_top", { n: 5 }) -> "Top 5 des actions". */
(function () {
  function PARTr(lang) {
    const D = window.PAR_I18N || { languages: { en: { lang_tag: "en-GB", claude: "British English" } }, strings: {} };
    lang = D.languages[lang] ? lang : "en";
    const t = (key, kw) => {
      const e = D.strings[key];
      const s = (e && (e[lang] || e.en)) || key;
      return kw ? s.replace(/\{(\w+)\}/g, (m, k) => (k in kw ? String(kw[k]) : m)) : s;
    };
    t.lang = lang;
    t.tag = D.languages[lang].lang_tag;
    t.claudeName = D.languages[lang].claude;
    t.quadrant = (q) => t(`q_${q}`);
    // Low / Medium / High agreeing with the noun (complexity is feminine in French and Italian, gain masculine).
    t.level = (v, kind) => { const k = `${kind === "gain" ? "gain" : "lvl"}_${v}`; return v && D.strings[k] ? t(k) : v; };
    t.yesNo = (v) => {
      const x = String(v || "").trim().toLowerCase();
      if (["yes", "y", "oui", "sí", "si", "sì"].includes(x)) return t("yes");
      if (["no", "n", "non"].includes(x)) return t("no");
      return v;
    };
    t.join = (xs) => (xs.length === 1 ? xs[0] : `${xs.slice(0, -1).join(", ")} ${t("and")} ${xs[xs.length - 1]}`);
    t.sep = lang === "fr" ? " : " : ": "; // French puts a space before the colon
    return t;
  }
  window.PARTr = PARTr;
  window.PARLanguages = () => Object.entries((window.PAR_I18N || {}).languages || { en: { native: "English" } }).map(([code, l]) => [code, l.native]);
})();
