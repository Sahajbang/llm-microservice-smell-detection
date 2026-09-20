import { Route, Routes } from 'react-router-dom'
import { Nav } from './components/Nav'
import { Overview } from './pages/Overview'
import { GraphPage } from './pages/GraphPage'
import { Detection } from './pages/Detection'
import { Refactoring } from './pages/Refactoring'
import { Runs } from './pages/Runs'
import { RunDetail } from './pages/RunDetail'
import { Analyze } from './pages/Analyze'

export default function App() {
  return (
    <>
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/analyze" element={<Analyze />} />
          <Route path="/analyze/:jobId" element={<Analyze />} />
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
