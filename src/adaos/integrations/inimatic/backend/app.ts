import express from 'express'
import http from 'http'
import { v4 as uuidv4 } from 'uuid'
import { Server, Socket } from 'socket.io'
import { createClient } from 'redis'
import fs from 'fs'
import { stat } from 'fs/promises'
import { installAdaosBridge } from './adaos-bridge.js'

type FollowerData = {
	followerName: string
	sessionId: string
}

type Follower = {
	[followerSocketId: string]: string
}

type SessionData = {
	initiatorSocketId: string
	followers: Follower
	timestamp: Date
}

type PublicSessionData = SessionData & {
	type: 'public'
	fileNames: Array<{ fileName: string; timestamp: string }>
}

type PrivateSessionData = SessionData & {
	type: 'private'
}

type UnionSessionData = PublicSessionData | PrivateSessionData

type CommunicationData = {
	isInitiator: boolean
	sessionId: string
	data: any
}

type StreamInfo = {
	stream: fs.WriteStream
	destroyTimeout: NodeJS.Timeout
	timestamp: string
}

type OpenedStreams = {
	[sessionId: string]: {
		[fileName: string]: StreamInfo
	}
}


const app = express()

app.use((req, res) => {
	res.status(404).send('Resource not found')
})

const server = http.createServer(app)
const io = new Server(server, {
	cors: { origin: '*' },
	pingTimeout: 10000,
	pingInterval: 10000,
})

installAdaosBridge(app, server)
const url = `redis://${process.env['PRODUCTION'] ? 'redis' : 'localhost'}:6379`
const redisClient = await createClient({ url })
	.on('error', (err) => console.log('Redis Client Error', err))
	.connect()

function isValidGuid(guid: string) {
	return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
		guid
	)
}

const openedStreams: OpenedStreams = {}
const FILESPATH = '/tmp/inimatic_public_files/'

if (!fs.existsSync(FILESPATH)) {
	fs.mkdirSync(FILESPATH)
}

function saveFileChunk(
	sessionId: string,
	fileName: string,
	content: Array<number>
) {
	if (!openedStreams[sessionId]) {
		openedStreams[sessionId] = {}
	}

	if (!openedStreams[sessionId][fileName]) {
		const timestamp = String(Date.now())
		const stream = fs.createWriteStream(
			FILESPATH + timestamp + '_' + fileName
		)
		const destroyTimeout = setTimeout(() => {
			openedStreams[sessionId][fileName].stream.destroy()
			fs.unlink(
				FILESPATH +
				openedStreams[sessionId][fileName].timestamp +
				'_' +
				fileName,
				() => { }
			)
			delete openedStreams[sessionId][fileName]
			console.log('destroy', openedStreams)
		}, 30000)

		openedStreams[sessionId][fileName] = {
			stream,
			destroyTimeout,
			timestamp,
		}
	}

	clearTimeout(openedStreams[sessionId][fileName].destroyTimeout)
	openedStreams[sessionId][fileName].destroyTimeout = setTimeout(() => {
		openedStreams[sessionId][fileName].stream.destroy()
		fs.unlink(
			FILESPATH +
			openedStreams[sessionId][fileName].timestamp +
			'_' +
			fileName,
			(error) => {
				if (error) console.log(error)
			}
		)
		delete openedStreams[sessionId][fileName]
		console.log('destroy', openedStreams)
	}, 30000)

	return new Promise<void>((resolve, reject) =>
		openedStreams[sessionId][fileName].stream.write(
			new Uint8Array(content),
			(error) => (error ? reject(error) : resolve())
		)
	)
}

io.on('connect', (socket) => {
	console.log(socket.id)

	socket.on('disconnecting', async () => {
		const rooms = Array.from(socket.rooms).filter(
			(roomId) => roomId != socket.id
		)
		if (!rooms.length) return

		const sessionId = rooms[0]
		console.log('disconnect', socket.id, socket.rooms, sessionId)
		const sessionData: UnionSessionData = JSON.parse(
			(await redisClient.get(sessionId))!
		)

		if (sessionData == null) {
			socket.to(sessionId).emit('initiator_disconnect')
			return
		}

		let isInitiator = sessionData.initiatorSocketId === socket.id
		if (isInitiator) {
			if (sessionData.type === 'public') {
				await Promise.all(
					sessionData.fileNames.map((item) => {
						const path =
							FILESPATH + item.timestamp + '_' + item.fileName

						return new Promise<void>((resolve) =>
							fs.unlink(path, () => resolve())
						)
					})
				)
			}

			socket.to(sessionId).emit('initiator_disconnect')
			io.socketsLeave(sessionId)
			await redisClient.del(sessionId)
			// delete saved files
		} else {
			io.to(sessionData.initiatorSocketId).emit(
				'follower_disconnect',
				sessionData.followers[socket.id]
			)

			delete sessionData.followers[socket.id]
			await redisClient.set(sessionId, JSON.stringify(sessionData))
		}
	})

	socket.on('add_initiator', async (type) => {
		const guid = uuidv4()
		let sessionData: UnionSessionData
		if (type === 'private') {
			sessionData = {
				initiatorSocketId: socket.id,
				followers: {},
				timestamp: new Date(),
				type: type,
			}
		} else {
			sessionData = {
				initiatorSocketId: socket.id,
				followers: {},
				timestamp: new Date(),
				type: type,
				fileNames: [],
			}
		}

		await redisClient.set(guid, JSON.stringify(sessionData))
		await redisClient.expire(guid, 3600)

		socket.join(guid)
		socket.emit('session_id', guid)
	})

	async function sendToPublicFollower(socket: Socket, emitObject: any) {
		return new Promise<void>((resolve) => {
			socket.emit('connection', emitObject, () => resolve())
		})
	}

	async function distributeSessionFiles(
		socket: Socket,
		fileNames: Array<{ fileName: string; timestamp: string }>
	) {
		const chunksize = 64 * 1024

		for (let item of fileNames) {
			const path = FILESPATH + item.timestamp + '_' + item.fileName
			const readStream = fs.createReadStream(path, {
				highWaterMark: chunksize,
				// encoding: 'utf8',
			})

			const size = (await stat(path)).size

			await sendToPublicFollower(socket, {
				type: 'transferFile',
				fileName: item.fileName,
				size: size,
			})
			console.log(size)

			for await (const chunk of readStream) {
				console.log('chunk', typeof chunk)

				await sendToPublicFollower(socket, {
					type: 'transferFile',
					fileName: item.fileName,
					size: size,
					content: new Uint8Array(chunk),
				})
			}

			await sendToPublicFollower(socket, {
				type: 'transferFile',
				fileName: item.fileName,
				size: size,
				end: true,
			})
		}
	}

	socket.on('add_follower', async (data) => {
		// возможно, стоит проверять наличие других комнат у сокета,
		// чтоб не было лишних подключений
		const { followerName, sessionId }: FollowerData = data
		if (!isValidGuid(sessionId)) return

		const sessionData: UnionSessionData = JSON.parse(
			(await redisClient.get(sessionId))!
		)
		console.log('add follower', followerName)

		if (sessionData === null) {
			socket.emit('initiator_disconnect')
			return
		}

		sessionData.followers[socket.id] = followerName
		socket.join(sessionId)

		await redisClient.set(sessionId, JSON.stringify(sessionData))

		io.to(sessionData.initiatorSocketId).emit(
			'connect_follower',
			followerName
		)

		if (sessionData.type === 'public') {
			await distributeSessionFiles(socket, sessionData.fileNames)
		}
	})

	socket.on('disconnect_follower', async (data) => {
		const { followerName, sessionId, isInitiator } = data
		if (!isValidGuid(sessionId)) return

		const sessionData: SessionData = JSON.parse(
			(await redisClient.get(sessionId))!
		)

		if (sessionData == null) {
			socket.emit('initiator_disconnect')
			return
		}

		if (isInitiator) {
			const socketIds = Object.keys(sessionData.followers).filter(
				(followerSocketId) =>
					sessionData.followers[followerSocketId] === followerName
			)
			console.log(socketIds)

			if (socketIds.length === 1) {
				delete sessionData.followers[socketIds[0]]
				await redisClient.set(sessionId, JSON.stringify(sessionData))
				const sockets = await io.sockets.fetchSockets()
				const followerSocket = sockets.filter(
					(socket) => socket.id === socketIds[0]
				)[0]
				if (followerSocket) {
					followerSocket.leave(sessionId)
					followerSocket.emit('initiator_disconnect')
				}
				socket.emit('follower_disconnect', followerName)
			}
		} else {
			delete sessionData.followers[socket.id]
			await redisClient.set(sessionId, JSON.stringify(sessionData))
			socket.leave(sessionId)
			socket.emit('initiator_disconnect')
			io.to(sessionData.initiatorSocketId).emit(
				'follower_disconnect',
				followerName
			)
		}
	})

	socket.on('conductor', async (data, fn) => {
		const receivedData: CommunicationData = data

		const sessionData: UnionSessionData = JSON.parse(
			(await redisClient.get(receivedData.sessionId))!
		)

		if (sessionData == null) {
			socket.to(receivedData.sessionId).emit('initiator_disconnect')
			return
		}

		if (sessionData.type === 'public') {
			const dataBody = receivedData.data
			if (dataBody.type === 'transferFile') {
				await saveFileChunk(
					receivedData.sessionId,
					dataBody.fileName,
					dataBody.content
				)

				if (dataBody.end) {
					clearTimeout(
						openedStreams[receivedData.sessionId][dataBody.fileName]
							.destroyTimeout
					)
					await new Promise<void>((resolve) =>
						openedStreams[receivedData.sessionId][
							dataBody.fileName
						].stream.close(() => resolve())
					)
					sessionData.fileNames.push({
						fileName: dataBody.fileName,
						timestamp:
							openedStreams[receivedData.sessionId][
								dataBody.fileName
							].timestamp,
					})
					await redisClient.set(
						receivedData.sessionId,
						JSON.stringify(sessionData)
					)
					delete openedStreams[receivedData.sessionId][
						dataBody.fileName
					]
					io.to(sessionData.initiatorSocketId).emit(
						'saved_file',
						dataBody.fileName
					)
				}
			}
		}
		await new Promise<void>((resolve) => {
			if (receivedData.isInitiator) {
				socket
					.to(receivedData.sessionId)
					.emit('connection', receivedData.data, () => resolve())
			} else {
				io.to(sessionData.initiatorSocketId).emit(
					'connection',
					receivedData.data,
					() => resolve()
				)
			}
		})

		if (fn) {
			fn(1)
		}
	})
})

const PORT = parseInt(process.env['PORT'] || '3030')
const HOST = process.env['HOST'] || '0.0.0.0'
server.listen(PORT, HOST, () =>
	console.log(`Started on http://${HOST}:${PORT} ...`)
)
