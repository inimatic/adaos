import { SignJWT, jwtVerify } from 'jose'

export type WebSessionJwtClaims = {
	sid?: string
	owner_id?: string
	subnet_id?: string
	hub_id?: string
	browser_key_id?: string
	stage?: string
}

const encoder = new TextEncoder()

export async function signWebSessionJwt(opts: {
	secret: string
	exp: number
	claims: WebSessionJwtClaims
}): Promise<string> {
	const key = encoder.encode(opts.secret)
	return await new SignJWT(opts.claims)
		.setProtectedHeader({ alg: 'HS256', typ: 'JWT' })
		.setIssuedAt()
		.setExpirationTime(opts.exp)
		.sign(key)
}

export async function verifyWebSessionJwt(opts: {
	secret: string
	token: string
}): Promise<WebSessionJwtClaims | null> {
	try {
		const key = encoder.encode(opts.secret)
		const { payload } = await jwtVerify(opts.token, key, { algorithms: ['HS256'] })
		return payload && typeof payload === 'object'
			? (payload as unknown as WebSessionJwtClaims)
			: null
	} catch {
		return null
	}
}

