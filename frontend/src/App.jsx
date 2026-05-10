import { useState, useEffect } from 'react'
import StatCard from './components/StatCard'
import VideoHistory from './components/VideoHistory'
import './index.css'

const API = 'http://localhost:8000'

export default function App() {
  // useState guarda datos que pueden cambiar.
  // Cuando cambian, React re-dibuja el componente automáticamente.
  const [stats, setStats] = useState(null)
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // useEffect se ejecuta una sola vez cuando el componente carga,
  // igual que componentDidMount en React de clases.
  useEffect(() => {
    async function fetchData() {
      try {
        // Llamamos a los dos endpoints en paralelo con Promise.all
        const [statsRes, videosRes] = await Promise.all([
          fetch(`${API}/api/stats`),
          fetch(`${API}/api/videos`),
        ])

        // .json() convierte la respuesta HTTP en un objeto JavaScript
        const statsData = await statsRes.json()
        const videosData = await videosRes.json()

        setStats(statsData)
        setVideos(videosData)
      } catch (err) {
        setError('No se pudo conectar con la API. ¿Está corriendo el servidor?')
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, []) // El [] significa "ejecutar solo al montar, no en cada render"

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-zinc-400">
        Cargando...
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="bg-red-950 border border-red-800 rounded-2xl p-6 text-red-300 max-w-md text-center">
          <p className="text-xl mb-2">⚠️ Error</p>
          <p>{error}</p>
          <p className="text-sm mt-3 text-red-400">
            Corré en otra terminal:{' '}
            <code className="bg-red-900 px-2 py-1 rounded">
              uvicorn api.main:app --port 8000
            </code>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen p-8 max-w-4xl mx-auto">

      {/* Header */}
      <header className="mb-10">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-3xl">🤖</span>
          <h1 className="text-3xl font-bold text-white">YouTube AI Manager</h1>
        </div>
        <p className="text-zinc-400">
          Dashboard del canal <strong className="text-white">{stats?.canal}</strong>
        </p>
      </header>

      {/* Tarjetas de estadísticas */}
      <section className="grid grid-cols-2 gap-4 mb-10">
        <StatCard
          icon="🎬"
          value={stats?.total_videos ?? 0}
          label="Videos subidos"
        />
        <StatCard
          icon="📅"
          value={
            stats?.ultimo_video
              ? new Date(stats.ultimo_video.fecha).toLocaleDateString('es-AR', {
                  day: '2-digit',
                  month: 'short',
                  year: 'numeric',
                })
              : '—'
          }
          label="Último upload"
        />
      </section>

      {/* Último video destacado */}
      {stats?.ultimo_video && (
        <section className="mb-10">
          <h2 className="text-lg font-semibold text-zinc-300 mb-3">Último video</h2>
          <a
            href={stats.ultimo_video.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block bg-zinc-900 border border-zinc-800 hover:border-red-700 rounded-2xl p-5 transition-colors group"
          >
            <p className="text-white font-medium group-hover:text-red-400 transition-colors">
              {stats.ultimo_video.titulo}
            </p>
            <p className="text-sm text-zinc-500 mt-1">Ver en YouTube ↗</p>
          </a>
        </section>
      )}

      {/* Historial de videos */}
      <section>
        <h2 className="text-lg font-semibold text-zinc-300 mb-3">
          Historial de uploads
        </h2>
        <VideoHistory videos={videos} />
      </section>

    </div>
  )
}
