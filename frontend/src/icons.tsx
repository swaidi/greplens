type P = { size?: number };
const base = (size: number) => ({ width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true });

export const Logo = ({ size = 26 }: P) => (
  <svg {...base(size)} stroke="#0B6576" strokeWidth={2.2}><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 21 21" /><path d="M8 10.5h5M8 8h3" /></svg>
);
export const IconDoc = ({ size = 15 }: P) => <svg {...base(size)}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6" /></svg>;
export const IconDownload = ({ size = 15 }: P) => <svg {...base(size)}><path d="M12 3v12M7 10l5 5 5-5M5 21h14" /></svg>;
export const IconShare = ({ size = 15 }: P) => <svg {...base(size)}><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M16 6l-4-4-4 4M12 2v13" /></svg>;
export const IconPlus = ({ size = 15 }: P) => <svg {...base(size)}><path d="M12 5v14M5 12h14" /></svg>;
export const IconSearch = ({ size = 15 }: P) => <svg {...base(size)}><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>;
export const IconClose = ({ size = 14 }: P) => <svg {...base(size)}><path d="M18 6 6 18M6 6l12 12" /></svg>;
export const IconCheck = ({ size = 14 }: P) => <svg {...base(size)}><path d="M20 6 9 17l-5-5" /></svg>;
export const IconHighlight = ({ size = 16 }: P) => <svg {...base(size)}><path d="m9 11-6 6v3h9l3-3" /><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4" /></svg>;
export const IconUnderline = ({ size = 16 }: P) => <svg {...base(size)}><path d="M6 4v6a6 6 0 0 0 12 0V4M4 20h16" /></svg>;
export const IconChevron = ({ size = 14 }: P) => <svg {...base(size)}><path d="m6 9 6 6 6-6" /></svg>;
export const IconBack = ({ size = 16 }: P) => <svg {...base(size)}><path d="m15 18-6-6 6-6" /></svg>;
export const IconRefresh = ({ size = 18 }: P) => <svg {...base(size)}><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5" /></svg>;
export const IconTrash = ({ size = 14 }: P) => <svg {...base(size)}><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" /></svg>;
export const IconGear = ({ size = 20 }: P) => (
  <svg {...base(size)}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
);
