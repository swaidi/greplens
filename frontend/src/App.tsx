import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { I18n, Lang, translator } from "./i18n";
import { applyTheme, Theme } from "./theme";
import Home from "./components/Home";
import Setup from "./components/Setup";
import Settings from "./components/Settings";
import WorkspaceView from "./components/Workspace";

export type View = { name: "home" } | { name: "setup" } | { name: "settings"; back?: View } | { name: "workspace"; id: string };

export default function App() {
  const [lang, setLangState] = useState<Lang>("en");
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    applyTheme("system");
    api.get<{ language: Lang; setup_complete: boolean; theme: Theme }>("/api/settings")
      .then((s) => { applyTheme(s.theme); setLangState(s.language === "ar" ? "ar" : "en"); setView(s.setup_complete ? { name: "home" } : { name: "setup" }); })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }, [lang]);

  const i18n = useMemo(() => ({
    lang, t: translator(lang),
    setLang: (l: Lang) => { setLangState(l); api.patch("/api/settings", { language: l }).catch(() => {}); },
  }), [lang]);

  if (error) return <div className="page"><div className="error-box">GrepLens could not start: {error}</div></div>;
  if (!view) return null;
  return (
    <I18n.Provider value={i18n}>
      {view.name === "setup" && <Setup onDone={() => setView({ name: "home" })} />}
      {view.name === "home" && <Home go={setView} />}
      {view.name === "settings" && <Settings go={setView} back={view.back ?? { name: "home" }} />}
      {view.name === "workspace" && <WorkspaceView id={view.id} go={setView} />}
    </I18n.Provider>
  );
}
