#import "AdaVoiceActivation.h"
#import "AdaVoiceDetector.h"
#import "AdaVoiceMetrics.h"
#import "AdaAppStorage.h"
#import "AdaSubnetClient.h"
#import "AdaSubnetSession.h"
#import "LabAudioProbe.h"
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <UIKit/UIKit.h>
#import <fcntl.h>
#import <unistd.h>
#import <libkern/OSAtomic.h>
#import <math.h>

static NSString *const Key = @"adaos.ios.voice-activation.v1";
static NSString *Job(void) { return [AdaAppStorageDirectory() stringByAppendingPathComponent:@"activation-probe.json"]; }
static NSArray *Words(NSString *text) {
    if (![text isKindOfClass:[NSString class]] || text.length > 8192) return @[];
    NSMutableArray *words = [NSMutableArray array];
    for (NSString *word in [[text lowercaseString] componentsSeparatedByCharactersInSet:[[NSCharacterSet alphanumericCharacterSet] invertedSet]])
        if (word.length) [words addObject:[word stringByReplacingOccurrencesOfString:@"ё" withString:@"е"]];
    return words;
}
@interface AdaVoiceActivation () {
@public
    AudioQueueRef _queue;
    AdaVoiceDetector *_detector;
    volatile int32_t _enqueued;
    volatile int32_t _overrunSignalled;
}
@property(nonatomic, readwrite) BOOL running;
@property(nonatomic, readwrite, copy) NSString *state;
@property(nonatomic, weak) AdaSubnetClient *client;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) NSSet *baseline;
@property(nonatomic, copy) NSString *target;
@property(nonatomic) BOOL probeWaiting, busy, waitingReply, suppressed;
@property(nonatomic) BOOL fixtureWaiting, fixture;
@property(nonatomic, copy) NSString *pendingOperation;
@property(nonatomic) BOOL recorderTrial, fixedQueue;
@property(nonatomic, strong) LabAudioProbe *recorder;
@property(nonatomic, strong) NSMutableData *fixedPCM;
@property(nonatomic, copy) NSString *firstWordClass;
@property(nonatomic, strong) NSDictionary *pcmMetrics;
@property(nonatomic) NSUInteger generation, captureGeneration, bytes, segments, recognized, addressed, ignored, claims, dispatched, replies, errors, overruns;
@property(nonatomic) NSInteger audioError;
@property(nonatomic) NSUInteger lastBufferBytes;
@property(nonatomic) NSUInteger bareAddresses, lastWordCount, lastDurationMS;
@property(nonatomic) double peakLevel;
@property(nonatomic) NSTimeInterval deadline, requestStarted, cooldown, started;
@property(nonatomic) BOOL oldIdleDisabled;
- (void)receive:(NSData *)data generation:(NSUInteger)generation;
- (void)segment:(const int16_t *)samples count:(size_t)count;
@end

static void Input(void *context, AudioQueueRef queue, AudioQueueBufferRef buffer,
                  const AudioTimeStamp *time, UInt32 packets, const AudioStreamPacketDescription *descriptions) {
    (void)time; (void)packets; (void)descriptions;
    AdaVoiceActivation *owner = (__bridge AdaVoiceActivation *)context;
    @autoreleasepool {
        int32_t pending = OSAtomicIncrement32Barrier(&owner->_enqueued);
        if (pending <= 8) {
            NSData *data = [NSData dataWithBytes:buffer->mAudioData length:buffer->mAudioDataByteSize];
            NSUInteger generation = owner.captureGeneration;
            dispatch_async(dispatch_get_main_queue(), ^{
                OSAtomicDecrement32Barrier(&owner->_enqueued);
                [owner receive:data generation:generation];
            });
        } else {
            OSAtomicDecrement32Barrier(&owner->_enqueued);
            NSUInteger generation = owner.captureGeneration;
            if (OSAtomicCompareAndSwap32Barrier(0, 1, &owner->_overrunSignalled))
                dispatch_async(dispatch_get_main_queue(), ^{ if (owner.running && owner->_queue && generation == owner.captureGeneration) { owner.overruns++; [owner stop:@"capture-overrun"]; } });
        }
        AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
    }
}
static void Segment(void *context, const int16_t *samples, size_t count) {
    [(__bridge AdaVoiceActivation *)context segment:samples count:count];
}
@implementation AdaVoiceActivation
+ (BOOL)addressed:(NSString *)text {
    NSArray *words = Words(text);
    return words.count > 1 && [words[0] isEqual:@"арсений"];
}
+ (NSString *)targetFromDescriptor:(NSArray *)targets {
    if (![targets isKindOfClass:[NSArray class]] || targets.count > 64) return nil;
    NSString *selected = nil;
    for (id value in targets) {
        if (![value isKindOfClass:[NSDictionary class]]) continue;
        NSString *identifier = value[@"id"];
        if (![identifier isKindOfClass:[NSString class]] || !identifier.length || identifier.length > 256) continue;
        NSMutableArray *aliases = [NSMutableArray array];
        if ([value[@"label"] isKindOfClass:[NSString class]]) [aliases addObject:value[@"label"]];
        if ([value[@"aliases"] isKindOfClass:[NSArray class]] && [value[@"aliases"] count] <= 16) [aliases addObjectsFromArray:value[@"aliases"]];
        for (id alias in aliases) {
            if (![alias isKindOfClass:[NSString class]] || [alias rangeOfString:@":"].location != NSNotFound) continue;
            NSArray *words = Words(alias);
            if (words.count == 1 && [words[0] isEqual:@"арсений"]) {
                if (selected && ![selected isEqual:identifier]) return nil;
                selected = identifier;
            }
        }
    }
    return selected;
}
+ (BOOL)prepareProbe {
    return [self prepareOperation:@"arseniy-foreground-90s"];
}
+ (BOOL)prepareFixtureProbe {
    return [self prepareOperation:@"arseniy-fixture-check"];
}
+ (BOOL)prepareRecorderProbe { return [self prepareOperation:@"arseniy-recorder-check"]; }
+ (BOOL)prepareQueueProbe { return [self prepareOperation:@"arseniy-queue-check"]; }
+ (NSData *)pcmFromWAV:(NSData *)wav {
    if (![wav isKindOfClass:[NSData class]] || wav.length < 44 || wav.length > 384044) return nil;
    const uint8_t *b = wav.bytes;
    if (memcmp(b,"RIFF",4) || memcmp(b+8,"WAVE",4)) return nil;
    BOOL format = NO; NSData *pcm = nil;
    for (NSUInteger offset=12; offset+8<=wav.length;) {
        const uint8_t *chunk = b+offset;
        uint32_t length = (uint32_t)chunk[4] | ((uint32_t)chunk[5]<<8) | ((uint32_t)chunk[6]<<16) | ((uint32_t)chunk[7]<<24);
        if (length > wav.length-offset-8) return nil;
        if (!memcmp(chunk,"fmt ",4)) {
            if (format || length < 16) return nil;
            const uint8_t *f=chunk+8;
            if (f[0]!=1 || f[1] || f[2]!=1 || f[3] || f[4]!=0x80 || f[5]!=0x3e || f[6] || f[7] ||
                f[8]!=0 || f[9]!=0x7d || f[10] || f[11] || f[12]!=2 || f[13] || f[14]!=16 || f[15]) return nil;
            format=YES;
        }
        if (!memcmp(chunk,"data",4)) {
            if (pcm || !length || length%2) return nil;
            pcm=[wav subdataWithRange:NSMakeRange(offset+8,length)];
        }
        offset+=8+(NSUInteger)length+(length&1);
    }
    return format ? pcm : nil;
}
+ (BOOL)prepareOperation:(NSString *)operation {
    NSInteger status;
    if (getuid() != 501 || ![AdaSubnetSession read:&status] || !AdaEnsurePrivateStorageDirectory()) return NO;
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"operation": operation, @"expires_at": @([NSDate date].timeIntervalSince1970 + 90)} options:0 error:nil];
    int fd = open(Job().fileSystemRepresentation, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0600);
    if (fd < 0) return NO;
    BOOL ok = write(fd, data.bytes, data.length) == (ssize_t)data.length; close(fd);
    if (!ok) unlink(Job().fileSystemRepresentation);
    return ok;
}
+ (NSDictionary *)report {
    id stored = [[NSUserDefaults standardUserDefaults] objectForKey:Key];
    if (![stored isKindOfClass:[NSDictionary class]]) return @{@"state": @"absent"};
    NSMutableDictionary *safe = [NSMutableDictionary dictionary];
    NSSet *states = [NSSet setWithArray:@[@"idle", @"starting", @"recording", @"listening", @"recognizing", @"claiming", @"waiting-reply", @"speaking", @"stopped", @"deadline", @"background", @"offline", @"audio-error", @"capture-overrun", @"target-unavailable", @"interrupted", @"permission-denied"]];
    if ([states containsObject:stored[@"state"]]) safe[@"state"] = stored[@"state"];
    if ([@[@"microphone", @"fixture", @"recorder", @"queue-fixed"] containsObject:stored[@"source"]]) safe[@"source"] = stored[@"source"];
    if ([@[@"empty", @"arseniy", @"reply", @"check", @"connection", @"other"] containsObject:stored[@"first_word_class"]]) safe[@"first_word_class"] = stored[@"first_word_class"];
    if ([@[@"builtin", @"headset", @"bluetooth", @"other", @"none"] containsObject:stored[@"input_route"]]) safe[@"input_route"] = stored[@"input_route"];
    for (NSString *key in @[@"running", @"captured_bytes", @"segments", @"recognized", @"addressed", @"bare_addresses", @"last_word_count", @"last_duration_ms", @"ignored", @"claims", @"dispatched", @"own_replies", @"errors", @"overruns", @"level", @"peak_level", @"last_buffer_bytes", @"noise", @"calibrated", @"too_long", @"audio_error", @"updated_at", @"elapsed_seconds", @"pcm_rms", @"tone_hz", @"tone_ppm", @"clipped_samples", @"hardware_sample_rate"])
        if ([stored[key] isKindOfClass:[NSNumber class]]) safe[key] = stored[key];
    return safe;
}
- (instancetype)init {
    self = [super init]; if (!self) return nil;
    _detector = calloc(1, sizeof(AdaVoiceDetector)); if (!_detector) return nil; self.state = @"idle";
    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:Job() error:nil];
    if ([attrs[NSFileType] isEqual:NSFileTypeRegular] && [attrs[NSFileOwnerAccountID] unsignedIntegerValue] == getuid() &&
        ([attrs[NSFilePosixPermissions] unsignedIntegerValue] & 0077) == 0 && [attrs[NSFileSize] unsignedLongLongValue] <= 512) {
        NSData *data = [NSData dataWithContentsOfFile:Job()];
        id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if ([[NSFileManager defaultManager] removeItemAtPath:Job() error:nil] && [value isKindOfClass:[NSDictionary class]] && [value count] == 2 &&
            [@[@"arseniy-foreground-90s", @"arseniy-fixture-check", @"arseniy-recorder-check", @"arseniy-queue-check"] containsObject:value[@"operation"]] && [value[@"expires_at"] isKindOfClass:[NSNumber class]]) {
            double expiry = [value[@"expires_at"] doubleValue];
            self.probeWaiting = isfinite(expiry) && expiry > [NSDate date].timeIntervalSince1970 && expiry <= [NSDate date].timeIntervalSince1970 + 120;
            self.fixtureWaiting = self.probeWaiting && [value[@"operation"] isEqual:@"arseniy-fixture-check"];
            self.pendingOperation = self.probeWaiting ? value[@"operation"] : nil;
        }
    }
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(interrupted:) name:AVAudioSessionInterruptionNotification object:nil];
    return self;
}
- (void)save {
    if (!_detector) return;
    NSString *source = self.fixture ? @"fixture" : self.recorderTrial ? @"recorder" : self.fixedQueue ? @"queue-fixed" : @"microphone";
    AVAudioSession *audio = [AVAudioSession sharedInstance]; NSString *port = audio.currentRoute.inputs.firstObject.portType;
    NSString *input = !port ? @"none" : [port isEqual:AVAudioSessionPortBuiltInMic] ? @"builtin" : [port isEqual:AVAudioSessionPortHeadsetMic] ? @"headset" : [port hasPrefix:@"Bluetooth"] ? @"bluetooth" : @"other";
    NSMutableDictionary *report = [@{@"state": self.state, @"running": @(self.running), @"source": source,
        @"input_route": input, @"hardware_sample_rate": @(audio.sampleRate),
        @"first_word_class": self.firstWordClass ?: @"empty",
        @"captured_bytes": @(self.bytes), @"segments": @(self.segments), @"recognized": @(self.recognized),
        @"addressed": @(self.addressed), @"ignored": @(self.ignored), @"claims": @(self.claims),
        @"bare_addresses": @(self.bareAddresses), @"last_word_count": @(self.lastWordCount), @"last_duration_ms": @(self.lastDurationMS),
        @"dispatched": @(self.dispatched), @"own_replies": @(self.replies), @"errors": @(self.errors), @"overruns": @(self.overruns),
        @"level": @(_detector->level), @"peak_level": @(self.peakLevel), @"last_buffer_bytes": @(self.lastBufferBytes),
        @"noise": @(_detector->noise), @"calibrated": @(_detector->calibration_count == 30),
        @"too_long": @(_detector->too_long), @"audio_error": @(self.audioError),
        @"updated_at": @([NSDate date].timeIntervalSince1970), @"elapsed_seconds": @(self.started ? [NSDate date].timeIntervalSince1970 - self.started : 0)} mutableCopy];
    if (self.pcmMetrics) [report addEntriesFromDictionary:self.pcmMetrics];
    [[NSUserDefaults standardUserDefaults] setObject:report forKey:Key];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
- (void)setStateValue:(NSString *)state { self.state = state; [self save]; if (self.changed) self.changed(); }
- (void)closeCapture {
    self.captureGeneration++;
    if (_queue) { AudioQueueStop(_queue, true); AudioQueueDispose(_queue, true); _queue = NULL; }
    AdaVoiceDetectorReset(_detector, 0);
    [[AVAudioSession sharedInstance] setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
}
- (BOOL)openCapture {
    if (self.fixture || self.recorderTrial) return YES;
    if (!self.running || _queue) return _queue != NULL;
    AVAudioSession *audio = [AVAudioSession sharedInstance]; NSError *error = nil;
    if (![audio setCategory:AVAudioSessionCategoryPlayAndRecord withOptions:AVAudioSessionCategoryOptionDefaultToSpeaker error:&error] ||
        ![audio setMode:AVAudioSessionModeDefault error:&error] || ![audio setPreferredSampleRate:16000 error:&error] || ![audio setActive:YES error:&error]) {
        self.audioError = error.code; [self stop:@"audio-error"]; return NO;
    }
    AudioStreamBasicDescription format = {0}; format.mSampleRate = 16000; format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked;
    format.mBytesPerPacket = 2; format.mFramesPerPacket = 1; format.mBytesPerFrame = 2; format.mChannelsPerFrame = 1; format.mBitsPerChannel = 16;
    self.captureGeneration++; OSAtomicCompareAndSwap32Barrier(1, 0, &_overrunSignalled); AdaVoiceDetectorReset(_detector, 1);
    OSStatus status = AudioQueueNewInput(&format, Input, (__bridge void *)self, NULL, NULL, 0, &_queue);
    for (int i=0; !status && i<3; i++) {
        AudioQueueBufferRef buffer; status = AudioQueueAllocateBuffer(_queue, 640, &buffer);
        if (!status) status = AudioQueueEnqueueBuffer(_queue, buffer, 0, NULL);
    }
    if (!status) status = AudioQueueStart(_queue, NULL);
    if (status) { self.audioError = status; [self stop:@"audio-error"]; return NO; }
    [self setStateValue:@"listening"]; return YES;
}
- (BOOL)start:(AdaSubnetClient *)client {
    if (self.running) return YES;
    self.probeWaiting = NO;
    if ([UIApplication sharedApplication].applicationState != UIApplicationStateActive || !client.ready || !client.voiceReady) return NO;
    self.fixture = self.fixtureWaiting; self.fixtureWaiting = NO;
    self.recorderTrial = [self.pendingOperation isEqual:@"arseniy-recorder-check"];
    self.fixedQueue = [self.pendingOperation isEqual:@"arseniy-queue-check"]; self.pendingOperation = nil;
    if (!self.fixture && [AVAudioSession sharedInstance].recordPermission != AVAudioSessionRecordPermissionGranted) { [self setStateValue:@"permission-denied"]; return NO; }
    self.target = [AdaVoiceActivation targetFromDescriptor:client.activationTargets];
    if (!self.target) { [self setStateValue:@"target-unavailable"]; return NO; }
    self.client = client; self.running = YES; self.generation++;
    self.started = [NSDate date].timeIntervalSince1970; self.deadline = self.started + 90;
    self.bytes = self.segments = self.recognized = self.addressed = self.ignored = self.claims = self.dispatched = self.replies = self.errors = self.overruns = 0;
    _detector->too_long = 0; self.audioError = 0; self.peakLevel = 0; self.busy = self.waitingReply = self.suppressed = NO;
    self.bareAddresses = self.lastWordCount = self.lastDurationMS = 0;
    self.firstWordClass = @"empty"; self.fixedPCM = self.fixedQueue ? [NSMutableData dataWithCapacity:160000] : nil;
    self.pcmMetrics = nil;
    self.oldIdleDisabled = [UIApplication sharedApplication].idleTimerDisabled;
    [UIApplication sharedApplication].idleTimerDisabled = YES;
    self.timer = [NSTimer scheduledTimerWithTimeInterval:1 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
    [self setStateValue:@"starting"];
    if (self.recorderTrial) {
        self.busy = YES; NSUInteger generation = self.generation;
        __weak AdaVoiceActivation *weakSelf = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            AdaVoiceActivation *owner = weakSelf;
            if (!owner.running || owner.generation != generation) return;
            owner.recorder = [[LabAudioProbe alloc] init];
            [owner setStateValue:@"recording"];
            [owner.recorder captureWAVWithCompletion:^(NSData *wav, NSString *status) {
                (void)status;
                AdaVoiceActivation *current = weakSelf;
                if (!current.running || current.generation != generation) return;
                NSData *pcm = [AdaVoiceActivation pcmFromWAV:wav]; current.busy = NO;
                if (!pcm) { current.errors++; [current stop:@"audio-error"]; return; }
                current.bytes = pcm.length;
                [current segment:pcm.bytes count:pcm.length/2];
            }];
        });
        return YES;
    }
    if (self.fixture) {
        NSURL *url = [[NSBundle mainBundle] URLForResource:@"arseniy-check" withExtension:@"wav" subdirectory:@"Fixtures"];
        NSData *wav = url ? [NSData dataWithContentsOfURL:url] : nil;
        if (wav.length < 44 || wav.length > 384044 || memcmp(wav.bytes,"RIFF",4) != 0) { self.errors++; [self stop:@"audio-error"]; return NO; }
        [self segment:(const int16_t *)((const uint8_t *)wav.bytes+44) count:(wav.length-44)/2];
        return YES;
    }
    return [self openCapture];
}
- (void)tick:(NSTimer *)timer {
    (void)timer; NSTimeInterval now = [NSDate date].timeIntervalSince1970;
    if (now >= self.deadline) { [self stop:@"deadline"]; return; }
    if (!self.client.ready) { [self stop:@"offline"]; return; }
    if (self.waitingReply && now - self.requestStarted > 30) { self.waitingReply = NO; self.errors++; self.cooldown = now + 0.8; if ([self singleShot]) { [self stop:@"stopped"]; return; } }
    if (!self.busy && !self.waitingReply && !self.suppressed && now >= self.cooldown) [self openCapture];
    [self save];
}
- (void)receive:(NSData *)data generation:(NSUInteger)generation {
    if (!self.running || !_queue || generation != self.captureGeneration) return;
    self.lastBufferBytes = data.length;
    /* AudioQueue may return a short PCM buffer while converting the hardware
     * sample rate. The detector explicitly carries incomplete 20-ms frames. */
    if (data.length > 640 || data.length % 2) { self.audioError = -101; self.errors++; [self stop:@"audio-error"]; return; }
    if (!data.length) return;
    self.bytes += data.length;
    if (self.fixedQueue) {
        NSUInteger take = MIN(data.length, 160000-self.fixedPCM.length);
        [self.fixedPCM appendBytes:data.bytes length:take];
    }
    AdaVoiceDetectorPush(_detector, data.bytes, data.length/2, self.fixedQueue ? NULL : Segment, (__bridge void *)self);
    self.peakLevel = MAX(self.peakLevel, _detector->level);
    if (self.fixedQueue && self.fixedPCM.length == 160000) {
        NSData *pcm = self.fixedPCM; self.fixedPCM = nil;
        [self segment:pcm.bytes count:pcm.length/2];
    }
}
- (void)segment:(const int16_t *)samples count:(size_t)count {
    if (!self.running || self.busy || self.suppressed || !count || count >= ADA_VOICE_MAX) return;
    NSMutableData *wav = [NSMutableData dataWithLength:44 + count*2];
    uint8_t *b = wav.mutableBytes; AdaVoiceCopy(b,"RIFF",4); AdaVoiceCopy(b+8,"WAVEfmt ",8); AdaVoiceCopy(b+36,"data",4);
    uint32_t n = (uint32_t)(count*2), total = n+36, fmt = 16, rate = 16000, bytes = 32000;
    uint16_t one=1, two=2, bits=16;
    AdaVoiceCopy(b+4,&total,4); AdaVoiceCopy(b+16,&fmt,4); AdaVoiceCopy(b+20,&one,2); AdaVoiceCopy(b+22,&one,2);
    AdaVoiceCopy(b+24,&rate,4); AdaVoiceCopy(b+28,&bytes,4); AdaVoiceCopy(b+32,&two,2); AdaVoiceCopy(b+34,&bits,2); AdaVoiceCopy(b+40,&n,4); AdaVoiceCopy(b+44,samples,n);
    self.segments++; self.lastDurationMS = count/16; self.busy = YES; NSUInteger generation = self.generation;
    /* Copy the detector-owned samples before closing capture. Stop callbacks
     * before measuring a clip on this older CPU; queue shutdown itself can
     * also enqueue a stale overrun notification, which must not cancel ASR. */
    [self closeCapture];
    AdaVoiceMetrics metrics = AdaVoiceMeasure((const int16_t *)(b+44),count);
    self.pcmMetrics = @{@"pcm_rms": @(metrics.rms), @"tone_hz": @(metrics.tone_hz), @"tone_ppm": @(metrics.tone_ppm), @"clipped_samples": @(metrics.clipped)};
    [self setStateValue:@"recognizing"];
    __weak AdaVoiceActivation *weakSelf = self;
    BOOL sent = [self.client requestVoiceKind:@"voice.transcribe" payload:@{@"audio_b64": [wav base64EncodedStringWithOptions:0], @"duration_ms": @(count/16), @"lang": @"ru-RU"} completion:^(NSDictionary *data, BOOL ok) {
        AdaVoiceActivation *owner = weakSelf; if (!owner.running || generation != owner.generation) return;
        NSString *text = [data[@"text"] isKindOfClass:[NSString class]] ? data[@"text"] : @"";
        NSArray *words = Words(text); owner.lastWordCount = words.count;
        NSDictionary *classes = @{@"арсений": @"arseniy", @"ответь": @"reply", @"проверка": @"check", @"связи": @"connection"};
        owner.firstWordClass = words.count ? (classes[words[0]] ?: @"other") : @"empty";
        if (words.count == 1 && [words[0] isEqual:@"арсений"]) owner.bareAddresses++;
        owner.busy = NO;
        if (!ok) { owner.errors++; owner.cooldown = [NSDate date].timeIntervalSince1970 + 1; [owner setStateValue:@"listening"]; if ([owner singleShot]) [owner stop:@"stopped"]; return; }
        if (text.length) owner.recognized++;
        if (![AdaVoiceActivation addressed:text]) { owner.ignored++; [owner setStateValue:@"listening"]; if ([owner singleShot]) [owner stop:@"stopped"]; return; }
        owner.addressed++; owner.busy = YES; [owner setStateValue:@"claiming"];
        BOOL claiming = [owner.client requestVoiceKind:@"voice.activation.claim" payload:@{@"text": text, @"capture_id": [NSUUID UUID].UUIDString,
            @"observed_at_ms": @([NSDate date].timeIntervalSince1970*1000), @"activation_confidence": @1, @"window_ms": @280} completion:^(NSDictionary *claim, BOOL accepted) {
            AdaVoiceActivation *current = weakSelf; if (!current.running || generation != current.generation) return;
            current.busy = NO;
            if (accepted && [claim[@"admitted"] isEqual:@YES]) {
                current.claims++;
                current.baseline = [NSSet setWithArray:[current.client.messages valueForKey:@"id"]];
                current.waitingReply = YES; current.requestStarted = [NSDate date].timeIntervalSince1970;
                if ([current.client sendActivatedText:text target:current.target]) { current.dispatched++; [current setStateValue:@"waiting-reply"]; }
                else { current.waitingReply = NO; current.errors++; if ([current singleShot]) [current stop:@"stopped"]; }
            } else { current.ignored++; if (!accepted) current.errors++; [current setStateValue:@"listening"]; if ([current singleShot]) [current stop:@"stopped"]; }
        }];
        if (!claiming) { owner.busy = NO; owner.errors++; if ([owner singleShot]) [owner stop:@"stopped"]; }
    }];
    if (!sent) { self.busy = NO; self.errors++; if ([self singleShot]) [self stop:@"stopped"]; }
}
- (void)observe:(AdaSubnetClient *)client {
    if (self.probeWaiting && client.ready && client.voiceChecked) [self start:client];
    if (!self.running) return;
    if (!client.ready) { [self stop:@"offline"]; return; }
    if (self.waitingReply) for (NSDictionary *entry in client.messages) {
        if ([entry[@"from"] isEqual:@"assistant"] && [entry[@"automatic_speech"] boolValue] && ![self.baseline containsObject:entry[@"id"]]) {
            self.waitingReply = NO; self.replies++; [self pauseForSpeech];
            if (self.readReply) self.readReply(entry[@"text"]); else [self speechFinished];
            break;
        }
    }
}
- (void)pauseForSpeech { if (self.running) { self.suppressed = YES; [self closeCapture]; [self setStateValue:@"speaking"]; } }
- (BOOL)singleShot { return self.fixture || self.recorderTrial || self.fixedQueue; }
- (void)speechFinished { if (self.running) { self.suppressed = NO; if ([self singleShot]) { [self stop:@"stopped"]; return; } self.cooldown = [NSDate date].timeIntervalSince1970 + 0.8; [self setStateValue:@"listening"]; } }
- (void)stop:(NSString *)reason {
    self.probeWaiting = NO;
    if (self.running) {
        self.running = NO; self.generation++; [self.timer invalidate]; self.timer = nil;
        [self.recorder cancel]; self.recorder = nil; self.fixedPCM = nil;
        [self closeCapture]; [self.client cancelActivationRequests];
        [UIApplication sharedApplication].idleTimerDisabled = self.oldIdleDisabled;
    }
    self.busy = self.waitingReply = self.suppressed = NO; [self setStateValue:reason];
}
- (void)interrupted:(NSNotification *)notification {
    if ([notification.userInfo[AVAudioSessionInterruptionTypeKey] unsignedIntegerValue] == AVAudioSessionInterruptionTypeBegan && self.running) [self stop:@"interrupted"];
}
- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
    if (_queue) { AudioQueueStop(_queue, true); AudioQueueDispose(_queue, true); }
    free(_detector);
}
@end
