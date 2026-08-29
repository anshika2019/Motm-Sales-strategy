export function getGenerationLabel(rawMessage: string, trigger?: string): string {
  if (trigger === "generate_pitch") return "Generating sales pitch…";
  const text = rawMessage.toLowerCase();
  if (/whatsapp/.test(text)) return "Generating WhatsApp message…";
  if (/\bemail\b/.test(text)) return "Generating email…";
  if (/cold call|call script/.test(text)) return "Generating cold call script…";
  if (/meeting/.test(text)) return "Generating meeting script…";
  if (/follow[\s-]?up/.test(text)) return "Generating follow-up strategy…";
  if (/\bpitch\b/.test(text)) return "Generating sales pitch…";
  if (/objection/.test(text)) return "Generating objection response…";
  return "Generating sales strategy…";
}
