// StatCard muestra una sola estadística con ícono, número y etiqueta.
// Props: icon (emoji), value (número o texto), label (descripción)
export default function StatCard({ icon, value, label }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 flex flex-col items-center gap-2">
      <span className="text-4xl">{icon}</span>
      <span className="text-3xl font-bold text-white">{value}</span>
      <span className="text-sm text-zinc-400">{label}</span>
    </div>
  )
}
