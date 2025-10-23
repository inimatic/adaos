// src/adaos/integrations/inimatic/backend/io/bus/nats.ts
import { connect, NatsConnection, StringCodec, consumerOpts, createInbox, type JetStreamManager } from 'nats'

export class NatsBus {
	private nc!: NatsConnection
	private sc = StringCodec()

	constructor(private url: string) { }

	async connect() {
		this.nc = await connect({ servers: this.url })

		// Ensure streams via JetStream Manager
		const jsm: JetStreamManager = await this.nc.jetstreamManager()
		const ensure = async (name: string, subjects: string[]) => {
			try {
				await jsm.streams.add({ name, subjects, retention: 'limits', discard: 'old' })
			} catch {
				/* already exists */
			}
		}
		await ensure('TG_INPUT', ['tg.input.*'])
		await ensure('TG_OUTPUT', ['tg.output.*'])
		await ensure('TG_DLQ', ['tg.dlq.*'])
	}

	async publish_input(hub_id: string, envelope: any) {
		const js = this.nc.jetstream()
		await js.publish(`tg.input.${hub_id}`, this.sc.encode(JSON.stringify(envelope)))
	}

	async subscribe_output(bot_id: string, handler: (subject: string, data: Uint8Array) => Promise<void>) {
		const js = this.nc.jetstream()
		const durable = `tg-out.${bot_id}`
		const subj = `tg.output.${bot_id}.>`
		const opts = consumerOpts()
		opts.durable(durable)
		opts.deliverTo(createInbox())
		opts.ackNone()

		const sub = await js.subscribe(subj, opts)
			; (async () => {
				for await (const m of sub) {
					await handler(m.subject, m.data)
				}
			})().catch(() => { })
		return sub
	}

	async publish_dlq(stage: string, payload: any) {
		const js = this.nc.jetstream()
		await js.publish(`tg.dlq.${stage}`, this.sc.encode(JSON.stringify(payload)))
	}
}
