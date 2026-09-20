import { Route, Routes } from 'react-router-dom'
import { Nav } from './components/Nav'
import { Overview } from './pages/Overview'
import { GraphPage } from './pages/GraphPage'
import { Detection } from './pages/Detection'
import { Refactoring } from './pages/Refactoring'
import { Runs } from './pages/Runs'
import { RunDetail } from './pages/RunDetail'

export default function App() {
  return (
    <>
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/detection" element={<Detection />} />
          <Route path="/refactoring" element={<Refactoring />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
        </Routes>
      </main>
    </>
  )
}
