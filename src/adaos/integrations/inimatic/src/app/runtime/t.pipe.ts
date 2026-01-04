import { Pipe, PipeTransform } from '@angular/core'
import { I18nService } from './i18n.service'

@Pipe({
	name: 't',
	standalone: true,
	pure: false,
})
export class TPipe implements PipeTransform {
	constructor(private i18n: I18nService) {}

	transform(key: string, params?: Record<string, any>): string {
		return this.i18n.t(key, params)
	}
}

