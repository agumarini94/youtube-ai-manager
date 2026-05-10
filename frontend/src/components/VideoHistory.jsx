// VideoHistory recibe la lista de videos y los muestra como tabla.
// Props: videos (array de objetos con url, titulo, fecha)
export default function VideoHistory({ videos }) {
  if (!videos.length) {
    return <p className="text-zinc-500 text-center py-8">No hay videos todavía.</p>
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900 text-zinc-400 uppercase text-xs">
          <tr>
            <th className="px-4 py-3 text-left">#</th>
            <th className="px-4 py-3 text-left">Título</th>
            <th className="px-4 py-3 text-left">Fecha</th>
            <th className="px-4 py-3 text-left">Link</th>
          </tr>
        </thead>
        <tbody>
          {videos.map((video, index) => (
            <tr
              key={video.url}
              className="border-t border-zinc-800 hover:bg-zinc-900 transition-colors"
            >
              <td className="px-4 py-3 text-zinc-500">{index + 1}</td>
              <td className="px-4 py-3 text-white max-w-xs truncate">{video.titulo}</td>
              <td className="px-4 py-3 text-zinc-400 whitespace-nowrap">
                {/* Convertimos la fecha ISO a un formato legible */}
                {new Date(video.fecha).toLocaleDateString('es-AR', {
                  day: '2-digit',
                  month: 'short',
                  year: 'numeric',
                })}
              </td>
              <td className="px-4 py-3">
                <a
                  href={video.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-red-500 hover:text-red-400 font-medium"
                >
                  Ver ↗
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
