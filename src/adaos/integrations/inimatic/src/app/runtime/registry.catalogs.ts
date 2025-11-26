// src\adaos\integrations\inimatic\src\app\runtime\registry.catalogs.ts
import { ModalRegistry } from './registry'
import { CatalogModalComponent } from '../renderer/modals/catalog-modal.component'

ModalRegistry['catalog-apps'] = (cfg: any) => ({ component: CatalogModalComponent, inputs: cfg })
ModalRegistry['catalog-widgets'] = (cfg: any) => ({ component: CatalogModalComponent, inputs: cfg })

