import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Toaster } from '@/components/ui/sonner'
import { AppSidebar } from './components/AppSidebar'
import { HomePage } from './pages/HomePage'
import { ReviewPage } from './pages/ReviewPage'
import { EditPage } from './pages/EditPage'
import { UploadPage } from './pages/UploadPage'
import { StudioPage } from './pages/StudioPage'
import { PipelinePage } from './pages/PipelinePage'
import { SchedulePage } from './pages/SchedulePage'
import { GrowthPage } from './pages/GrowthPage'
import { PipelineContext, usePipelineProvider } from './hooks/usePipeline'
import { ChannelScopeContext, useChannelScopeProvider } from './hooks/useChannelScope'

export default function App() {
  const pipeline = usePipelineProvider()
  const channelScope = useChannelScopeProvider()

  return (
    <BrowserRouter>
      <PipelineContext.Provider value={pipeline}>
        <ChannelScopeContext.Provider value={channelScope}>
          <SidebarProvider>
            <div className="flex min-h-screen w-full">
              <AppSidebar />
              <main className="flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8">
                  <Routes>
                    <Route path="/" element={<HomePage />} />
                    <Route path="/review" element={<ReviewPage />} />
                    <Route path="/edit" element={<EditPage />} />
                    <Route path="/upload" element={<UploadPage />} />
                    <Route path="/studio" element={<StudioPage />} />
                    <Route path="/pipeline" element={<PipelinePage />} />
                    <Route path="/schedule" element={<SchedulePage />} />
                    <Route path="/growth" element={<GrowthPage />} />

                    {/* Legacy redirects */}
                    <Route path="/calendar" element={<Navigate to="/schedule" replace />} />
                    <Route path="/queue" element={<Navigate to="/studio" replace />} />
                    <Route path="/analytics" element={<Navigate to="/growth" replace />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </div>
              </main>
            </div>
            <Toaster />
          </SidebarProvider>
        </ChannelScopeContext.Provider>
      </PipelineContext.Provider>
    </BrowserRouter>
  )
}
