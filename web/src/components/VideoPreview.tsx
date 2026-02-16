import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { videoUrl } from '@/lib/api'

interface VideoPreviewProps {
  clipId: string | null
  title: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function VideoPreview({ clipId, title, open, onOpenChange }: VideoPreviewProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="truncate">{title}</DialogTitle>
        </DialogHeader>
        {clipId && (
          <video
            src={videoUrl(clipId)}
            controls
            autoPlay
            className="w-full rounded-md"
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
