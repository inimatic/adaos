import { Component, Input } from '@angular/core'
import { IonicModule, ModalController } from '@ionic/angular'
import { CommonModule } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { YDocService } from '../../y/ydoc.service'

@Component({
  selector: 'ada-weather-modal',
  standalone: true,
  imports: [IonicModule, CommonModule, FormsModule],
  templateUrl: './weather-modal.component.html',
  styles: [
    `:host{display:flex;flex-direction:column;height:100%} ion-content{flex:1 1 auto}`
  ]
})
export class WeatherModalComponent {
  @Input() title = '������'
  @Input() weather?: { city:string; temp_c:number; condition:string; wind_ms:number; updated_at:string }
  cities: string[] = ['Berlin', 'Moscow', 'New York', 'Tokyo', 'Paris']
  constructor(private modal: ModalController, private y: YDocService) {}
  close(){ this.modal.dismiss() }

  onCityChange(city: string){
    if (!city) return
    const doc = this.y.doc
    doc.transact(() => {
      const dataMap: any = this.y.doc.getMap('data')
      const currentWeather = this.y.toJSON(dataMap.get('weather')) || {}
      const nextWeather = {
        ...currentWeather,
        current: { ...(currentWeather.current || {}), city }
      }
      dataMap.set('weather', nextWeather)
    })
  }
}
