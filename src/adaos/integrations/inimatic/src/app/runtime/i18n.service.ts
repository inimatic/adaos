import { Injectable } from '@angular/core'
import { BehaviorSubject } from 'rxjs'

export type UiLang = 'en' | 'ru' | 'fr' | 'ch'

type Dict = Record<string, string>

const DICTS: Record<UiLang, Dict> = {
	en: {
		'lang.en': 'EN',
		'lang.ru': 'RU',
		'lang.fr': 'FR',
		'lang.ch': '中文',

		'status.checking': 'checking',
		'status.online': 'online',
		'status.offline': 'offline',

		'aria.logout_debug': 'Logout (debug)',
		'aria.reload': 'Reload',

		'pair.title': 'Pair this device',
		'pair.instructions': 'Open Inimatic on your phone and scan the QR code to connect this browser to your hub.',
		'pair.owner_login_debug': 'Owner login (debug)',
		'pair.api_label': 'pair api:',
		'pair.status.creating': 'creating pairing...',
		'pair.status.waiting': 'waiting for approval...',
		'pair.status.connecting': 'approved, connecting...',
		'pair.status.regenerating': 'pairing {state}, regenerating...',
		'pair.status.create_failed': 'failed to create pairing',

		'pair.approve.title': 'Approve device',
		'pair.approve.code': 'Code:',
		'pair.approve.login_prompt': 'Login to approve this device.',
		'pair.approve.webspace': 'Webspace',
		'pair.approve.button': 'Approve',
		'pair.approve.status.approving': 'approving...',
		'pair.approve.status.approved': 'approved',
		'pair.approve.status.failed': 'approve failed: {error}',

		'pair.hub_offline.title': 'Hub offline',
		'pair.hub_offline.desc': 'Local hub is not reachable. Login to route through root.',

		'login.choose_title': 'Choose an option:',
		'login.registration': 'Registration',
		'login.login': 'Log in',
		'login.register_title': 'Register with Code',
		'login.registration_code_label': 'Registration Code',
		'login.registration_code_placeholder': 'XXXX-XXXX',
		'login.register': 'Register',
		'login.processing': 'Processing...',
		'login.back': 'Back',
		'login.login_title': 'Log in with WebAuthn',
		'login.login_info': 'Click the button below to authenticate with your WebAuthn credential.',
		'login.authenticating': 'Authenticating...',

		'login.error.enter_code': 'Enter registration code',
		'login.error.webauthn': 'WebAuthn error: {msg}',
		'login.error.invalid_code': 'Invalid code or WebAuthn registration failed',
		'login.error.server': 'Server error',
		'login.error.unexpected': 'Unexpected error',
		'login.error.no_credentials': 'No WebAuthn credentials found. Please register first.',
		'login.error.auth_failed': 'WebAuthn authentication failed',
	},
	ru: {
		'lang.en': 'EN',
		'lang.ru': 'RU',
		'lang.fr': 'FR',
		'lang.ch': '中文',

		'status.checking': 'проверка',
		'status.online': 'онлайн',
		'status.offline': 'офлайн',

		'aria.logout_debug': 'Выйти (debug)',
		'aria.reload': 'Обновить',

		'pair.title': 'Подключить устройство',
		'pair.instructions': 'Откройте Inimatic на телефоне и отсканируйте QR‑код, чтобы подключить этот браузер к вашему хабу.',
		'pair.owner_login_debug': 'Вход владельца (debug)',
		'pair.api_label': 'pair api:',
		'pair.status.creating': 'создание кода...',
		'pair.status.waiting': 'ожидание подтверждения...',
		'pair.status.connecting': 'подтверждено, подключение...',
		'pair.status.regenerating': 'состояние {state}, пересоздаём...',
		'pair.status.create_failed': 'не удалось создать код',

		'pair.approve.title': 'Подтвердить устройство',
		'pair.approve.code': 'Код:',
		'pair.approve.login_prompt': 'Войдите, чтобы подтвердить это устройство.',
		'pair.approve.webspace': 'Веб‑пространство',
		'pair.approve.button': 'Подтвердить',
		'pair.approve.status.approving': 'подтверждение...',
		'pair.approve.status.approved': 'подтверждено',
		'pair.approve.status.failed': 'ошибка подтверждения: {error}',

		'pair.hub_offline.title': 'Хаб недоступен',
		'pair.hub_offline.desc': 'Локальный хаб недоступен. Войдите, чтобы работать через root.',

		'login.choose_title': 'Выберите действие:',
		'login.registration': 'Регистрация',
		'login.login': 'Войти',
		'login.register_title': 'Регистрация по коду',
		'login.registration_code_label': 'Код регистрации',
		'login.registration_code_placeholder': 'XXXX-XXXX',
		'login.register': 'Зарегистрировать',
		'login.processing': 'Обработка...',
		'login.back': 'Назад',
		'login.login_title': 'Вход через WebAuthn',
		'login.login_info': 'Нажмите кнопку ниже, чтобы выполнить вход с помощью WebAuthn.',
		'login.authenticating': 'Проверка...',

		'login.error.enter_code': 'Введите код регистрации',
		'login.error.webauthn': 'Ошибка WebAuthn: {msg}',
		'login.error.invalid_code': 'Неверный код или ошибка регистрации WebAuthn',
		'login.error.server': 'Ошибка сервера',
		'login.error.unexpected': 'Неожиданная ошибка',
		'login.error.no_credentials': 'Учетные данные WebAuthn не найдены. Сначала зарегистрируйтесь.',
		'login.error.auth_failed': 'Ошибка аутентификации WebAuthn',
	},
	fr: {
		'lang.en': 'EN',
		'lang.ru': 'RU',
		'lang.fr': 'FR',
		'lang.ch': '中文',

		'status.checking': 'vérification',
		'status.online': 'en ligne',
		'status.offline': 'hors ligne',

		'aria.logout_debug': 'Déconnexion (debug)',
		'aria.reload': 'Recharger',

		'pair.title': 'Appairer cet appareil',
		'pair.instructions': "Ouvrez Inimatic sur votre téléphone et scannez le QR code pour connecter ce navigateur à votre hub.",
		'pair.owner_login_debug': 'Connexion propriétaire (debug)',
		'pair.api_label': 'pair api:',
		'pair.status.creating': "création de l'appairage...",
		'pair.status.waiting': 'en attente de validation...',
		'pair.status.connecting': 'validé, connexion...',
		'pair.status.regenerating': 'état {state}, régénération...',
		'pair.status.create_failed': "impossible de créer l'appairage",

		'pair.approve.title': "Approuver l'appareil",
		'pair.approve.code': 'Code :',
		'pair.approve.login_prompt': "Connectez-vous pour approuver cet appareil.",
		'pair.approve.webspace': 'Espace web',
		'pair.approve.button': 'Approuver',
		'pair.approve.status.approving': 'approbation...',
		'pair.approve.status.approved': 'approuvé',
		'pair.approve.status.failed': "échec de l'approbation : {error}",

		'pair.hub_offline.title': 'Hub hors ligne',
		'pair.hub_offline.desc': "Le hub local est inaccessible. Connectez-vous pour passer par le root.",

		'login.choose_title': 'Choisissez une option :',
		'login.registration': 'Inscription',
		'login.login': 'Se connecter',
		'login.register_title': "S'inscrire avec un code",
		'login.registration_code_label': "Code d'inscription",
		'login.registration_code_placeholder': 'XXXX-XXXX',
		'login.register': "S'inscrire",
		'login.processing': 'Traitement...',
		'login.back': 'Retour',
		'login.login_title': 'Connexion WebAuthn',
		'login.login_info': "Cliquez sur le bouton ci-dessous pour vous authentifier avec votre identifiant WebAuthn.",
		'login.authenticating': 'Authentification...',

		'login.error.enter_code': "Entrez le code d'inscription",
		'login.error.webauthn': 'Erreur WebAuthn : {msg}',
		'login.error.invalid_code': 'Code invalide ou échec de l’inscription WebAuthn',
		'login.error.server': 'Erreur serveur',
		'login.error.unexpected': 'Erreur inattendue',
		'login.error.no_credentials': "Aucun identifiant WebAuthn trouvé. Veuillez d'abord vous inscrire.",
		'login.error.auth_failed': 'Échec de l’authentification WebAuthn',
	},
	ch: {
		'lang.en': 'EN',
		'lang.ru': 'RU',
		'lang.fr': 'FR',
		'lang.ch': '中文',

		'status.checking': '检查中',
		'status.online': '在线',
		'status.offline': '离线',

		'aria.logout_debug': '退出 (debug)',
		'aria.reload': '刷新',

		'pair.title': '配对此设备',
		'pair.instructions': '在手机上打开 Inimatic 并扫描二维码，将此浏览器连接到你的 Hub。',
		'pair.owner_login_debug': '所有者登录 (debug)',
		'pair.api_label': 'pair api:',
		'pair.status.creating': '正在创建配对码...',
		'pair.status.waiting': '等待确认...',
		'pair.status.connecting': '已确认，正在连接...',
		'pair.status.regenerating': '状态 {state}，正在重新生成...',
		'pair.status.create_failed': '创建配对失败',

		'pair.approve.title': '批准设备',
		'pair.approve.code': '代码：',
		'pair.approve.login_prompt': '登录以批准此设备。',
		'pair.approve.webspace': 'Webspace',
		'pair.approve.button': '批准',
		'pair.approve.status.approving': '正在批准...',
		'pair.approve.status.approved': '已批准',
		'pair.approve.status.failed': '批准失败：{error}',

		'pair.hub_offline.title': 'Hub 离线',
		'pair.hub_offline.desc': '本地 Hub 不可达。请登录以通过 root 连接。',

		'login.choose_title': '请选择：',
		'login.registration': '注册',
		'login.login': '登录',
		'login.register_title': '使用注册码注册',
		'login.registration_code_label': '注册码',
		'login.registration_code_placeholder': 'XXXX-XXXX',
		'login.register': '注册',
		'login.processing': '处理中...',
		'login.back': '返回',
		'login.login_title': '使用 WebAuthn 登录',
		'login.login_info': '点击下面按钮，使用你的 WebAuthn 凭据进行登录。',
		'login.authenticating': '验证中...',

		'login.error.enter_code': '请输入注册码',
		'login.error.webauthn': 'WebAuthn 错误：{msg}',
		'login.error.invalid_code': '注册码无效或 WebAuthn 注册失败',
		'login.error.server': '服务器错误',
		'login.error.unexpected': '未知错误',
		'login.error.no_credentials': '未找到 WebAuthn 凭据。请先注册。',
		'login.error.auth_failed': 'WebAuthn 认证失败',
	},
}

function normalizeLang(raw: string | null | undefined): UiLang {
	const v = String(raw || '').toLowerCase()
	if (v.startsWith('ru')) return 'ru'
	if (v.startsWith('fr')) return 'fr'
	if (v.startsWith('zh') || v.startsWith('ch')) return 'ch'
	return 'en'
}

@Injectable({ providedIn: 'root' })
export class I18nService {
	private readonly langSubject = new BehaviorSubject<UiLang>('en')
	readonly lang$ = this.langSubject.asObservable()

	constructor() {
		const stored = (() => {
			try {
				return (localStorage.getItem('adaos_lang') || '').trim()
			} catch {
				return ''
			}
		})()
		if (stored) {
			this.langSubject.next(normalizeLang(stored))
			return
		}
		const browser = (() => {
			try {
				const nav: any = navigator
				const first = Array.isArray(nav.languages) && nav.languages.length ? nav.languages[0] : nav.language
				return String(first || '')
			} catch {
				return ''
			}
		})()
		this.langSubject.next(normalizeLang(browser))
	}

	getLang(): UiLang {
		return this.langSubject.value
	}

	setLang(lang: UiLang): void {
		this.langSubject.next(lang)
		try {
			localStorage.setItem('adaos_lang', lang)
		} catch {}
	}

	t(key: string, params?: Record<string, any>): string {
		const lang = this.getLang()
		const dict = DICTS[lang] || DICTS.en
		const raw = dict[key] ?? DICTS.en[key] ?? key
		if (!params) return raw
		return raw.replace(/\{(\w+)\}/g, (_m, name) => {
			const v = params[name]
			return v === undefined || v === null ? '' : String(v)
		})
	}
}
