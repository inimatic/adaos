import { Component, EventEmitter, Output } from '@angular/core'
import { LoginService, type LoginResult } from './login.service'
import { IonButton, IonInput } from '@ionic/angular/standalone'
import { FormsModule } from '@angular/forms'
import { CommonModule } from '@angular/common'

@Component({
	selector: 'app-login',
	standalone: true,
	templateUrl: './login.component.html',
	styleUrl: './login.component.scss',
	imports: [IonInput, IonButton, FormsModule, CommonModule],
})
export class LoginComponent {
	@Output() loginSuccess = new EventEmitter<LoginResult>()

	// Registration mode
	userCode = ''
	registrationLoading = false
	registrationError = ''

	// Login mode
	loginLoading = false
	loginError = ''

	// Mode toggle
	mode: 'selection' | 'registration' | 'login' = 'selection'

	constructor(private loginService: LoginService) {}

	switchToRegistration() {
		this.mode = 'registration'
		this.registrationError = ''
		this.userCode = ''
	}

	switchToLogin() {
		this.mode = 'login'
		this.loginError = ''
	}

	backToSelection() {
		this.mode = 'selection'
		this.registrationError = ''
		this.loginError = ''
	}

	onRegister() {
		this.registrationError = ''

		if (!this.userCode.trim()) {
			this.registrationError = 'Enter registration code'
			return
		}

		this.registrationLoading = true

		this.loginService.register(this.userCode).subscribe({
			next: (result) => {
				this.registrationLoading = false
				this.loginSuccess.emit(result)
			},
			error: (err) => {
				this.registrationLoading = false

				if (err instanceof Error && !('status' in err)) {
					// Локальные ошибки (например, отсутствие поддержки WebAuthn)
					this.registrationError = err.message || 'WebAuthn error'
					return
				}

				const status = err.status ?? 0
				if (status === 400) {
					this.registrationError =
						'Invalid code or WebAuthn registration failed'
				} else if (status >= 500) {
					this.registrationError = 'Server error'
				} else {
					this.registrationError = 'Unexpected error'
				}
			},
		})
	}

	onLogin() {
		this.loginError = ''
		this.loginLoading = true

		this.loginService.login().subscribe({
			next: (result) => {
				this.loginLoading = false
				this.loginSuccess.emit(result)
			},
			error: (err) => {
				this.loginLoading = false

				if (err instanceof Error && !('status' in err)) {
					// Локальные ошибки (например, отсутствие поддержки WebAuthn)
					this.loginError = err.message || 'WebAuthn error'
					return
				}

				const status = err.status ?? 0
				if (status === 400) {
					const errorCode = err.error?.code || err.error?.error
					if (errorCode === 'no_credentials_registered') {
						this.loginError =
							'No WebAuthn credentials found. Please register first.'
					} else {
						this.loginError = 'WebAuthn authentication failed'
					}
				} else if (status >= 500) {
					this.loginError = 'Server error'
				} else {
					this.loginError = 'Unexpected error'
				}
			},
		})
	}
}
