import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Calendar,
  Film,
  ListChecks,
  SlidersHorizontal,
  TrendingUp,
  Workflow,
  Zap,
} from 'lucide-react'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from '@/components/ui/sidebar'
import { usePipeline } from '@/hooks/usePipeline'
import { useChannelScope } from '@/hooks/useChannelScope'
import { fetchQueue } from '@/lib/api'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const navItems = [
  { to: '/', label: 'Home', icon: Zap },
  { to: '/review', label: 'Review', icon: ListChecks, badgeKey: 'pending' as const },
  { to: '/edit', label: 'Edit', icon: SlidersHorizontal, badgeKey: 'approved' as const },
  { to: '/studio', label: 'Studio', icon: Film },
  { to: '/pipeline', label: 'Pipeline', icon: Workflow, pulsing: true },
  { to: '/schedule', label: 'Schedule', icon: Calendar },
  { to: '/growth', label: 'Growth', icon: TrendingUp },
]

export function AppSidebar() {
  const { state } = usePipeline()
  const isRunning = state !== null && state.running === true
  const { channel, setChannel, channels } = useChannelScope()
  const channelEntries = Object.entries(channels ?? {})

  // Badge counts
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    const channelKey = channel !== 'all' ? channel : undefined
    fetchQueue('pending', { limit: 1, channel: channelKey })
      .then((clips) => setPendingCount(clips.length > 0 ? clips.length : 0))
      .catch(() => {})
  }, [channel])

  return (
    <Sidebar>
      <SidebarHeader className="p-4 space-y-3">
        <span className="text-sm font-semibold text-foreground">clipper</span>
        {channelEntries.length > 0 && (
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Workspace
            </div>
            <Select value={channel} onValueChange={setChannel}>
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All channels</SelectItem>
                {channelEntries.map(([key, info]) => (
                  <SelectItem key={key} value={key}>
                    {info?.name ? info.name : key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </SidebarHeader>
      <SidebarSeparator />
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton asChild size="sm">
                    <NavLink
                      to={item.to}
                      end={item.to === '/'}
                      className={({ isActive }) =>
                        isActive
                          ? 'text-foreground bg-sidebar-accent'
                          : 'text-muted-foreground hover:text-sidebar-foreground'
                      }
                    >
                      <item.icon className="size-4" />
                      <span>{item.label}</span>
                      {item.pulsing && isRunning && (
                        <>
                          <span
                            className="ml-auto size-1.5 rounded-full bg-primary animate-pulse"
                            aria-hidden="true"
                          />
                          <span className="sr-only">(running)</span>
                        </>
                      )}
                      {item.badgeKey === 'pending' && pendingCount > 0 && (
                        <span className="ml-auto flex size-5 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                          {pendingCount > 99 ? '99+' : pendingCount}
                        </span>
                      )}
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  )
}
