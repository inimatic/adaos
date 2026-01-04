import { Component } from '@angular/core'
import { CommonModule } from '@angular/common'
import { IonicModule } from '@ionic/angular'
import { I18nService, UiLang } from '../../runtime/i18n.service'
import { TPipe } from '../../runtime/t.pipe'

@Component({
	selector: 'ada-language-picker',
	standalone: true,
	imports: [CommonModule, IonicModule, TPipe],
	template: `
		<div class="lang">
			<ion-segment [value]="(lang$ | async) ?? 'en'" (ionChange)="onChange($event)">
				<ion-segment-button value="en"><ion-label>{{ 'lang.en' | t }}</ion-label></ion-segment-button>
				<ion-segment-button value="ru"><ion-label>{{ 'lang.ru' | t }}</ion-label></ion-segment-button>
				<ion-segment-button value="fr"><ion-label>{{ 'lang.fr' | t }}</ion-label></ion-segment-button>
				<ion-segment-button value="ch"><ion-label>{{ 'lang.ch' | t }}</ion-label></ion-segment-button>
			</ion-segment>
		</div>
	`,
	styles: [`
		.lang {
			display: flex;
			justify-content: center;
			margin-bottom: 12px;
		}
		ion-segment {
			max-width: 360px;
		}
		ion-segment-button {
			min-width: 64px;
		}
	`],
})
export class LanguagePickerComponent {
	readonly lang$ = this.i18n.lang$

	constructor(private i18n: I18nService) {
	}

	onChange(ev: any): void {
		const v = String(ev?.detail?.value || '').trim()
		if (v === 'en' || v === 'ru' || v === 'fr' || v === 'ch') this.i18n.setLang(v)
	}
}
