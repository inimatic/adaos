import { ModalRegistry } from './registry'
import { WorkspaceManagerModalComponent } from '../renderer/modals/workspace-manager-modal.component'

ModalRegistry['workspace-manager'] = () => ({
  component: WorkspaceManagerModalComponent,
})

