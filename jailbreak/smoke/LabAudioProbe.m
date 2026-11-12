#import "LabAudioProbe.h"
#import "AdaAppStorage.h"
#import <dispatch/dispatch.h>

#define AudioDirectory AdaAppStorageDirectory()
static uint16_t LE16(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1] << 8)); }
static uint32_t LE32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

@interface LabAudioProbe ()
@property(nonatomic, readwrite, getter=isRunning) BOOL running;
@property(nonatomic, strong) AVAudioRecorder *recorder;
@property(nonatomic, strong) AVAudioPlayer *player;
@property(nonatomic, strong) NSTimer *deadline;
@property(nonatomic, strong) NSURL *file;
@property(nonatomic, copy) LabAudioCompletion completion;
@property(nonatomic) BOOL replayCapture;
@property(nonatomic, copy) NSString *captureReport;
@property(nonatomic, strong) NSDate *playbackStarted;
@property(nonatomic) BOOL returnValidatedData;
@property(nonatomic, strong) NSData *validatedData;
- (void)recordWithReplay:(BOOL)replay completion:(LabAudioCompletion)completion;
- (void)startPlayback:(NSData *)data;
@end

@implementation LabAudioProbe
- (BOOL)microphoneActive { return self.recorder.isRecording; }
+ (NSString *)permission {
    switch ([[AVAudioSession sharedInstance] recordPermission]) {
        case AVAudioSessionRecordPermissionGranted: return @"granted";
        case AVAudioSessionRecordPermissionDenied: return @"denied";
        default: return @"undetermined";
    }
}

+ (void)requestPermissionWithCompletion:(LabAudioCompletion)completion {
    [[AVAudioSession sharedInstance] requestRecordPermission:^(BOOL granted) {
        dispatch_async(dispatch_get_main_queue(), ^{
            completion(granted ? @"Microphone permission granted. No audio recorded." :
                                 @"Microphone permission denied. No audio recorded.");
        });
    }];
}

- (void)finish:(NSString *)status {
    if (!self.running) return;
    self.running = NO;
    [self.deadline invalidate];
    self.deadline = nil;
    self.recorder.delegate = nil;
    [self.recorder stop];
    self.recorder = nil;
    self.player.delegate = nil;
    [self.player stop];
    self.player = nil;
    self.playbackStarted = nil;
    BOOL removed = YES;
    BOOL hadFile = self.file != nil;
    if (self.file && [[NSFileManager defaultManager] fileExistsAtPath:self.file.path])
        removed = [[NSFileManager defaultManager] removeItemAtURL:self.file error:nil];
    self.file = nil;
    [[AVAudioSession sharedInstance] setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
    LabAudioCompletion completion = self.completion;
    self.completion = nil;
    self.captureReport = nil;
    if (completion) completion(!removed ? @"Audio cleanup failed. Inspect the private test directory." :
                              hadFile ? [status stringByAppendingString:@" Local test file removed."] : status);
}

- (void)deadlineFired:(NSTimer *)timer {
    (void)timer;
    [self finish:@"Audio capture deadline exceeded."];
}

- (void)recordShortSampleWithCompletion:(LabAudioCompletion)completion {
    [self recordWithReplay:NO completion:completion];
}

- (void)recordShortSampleAndReplayWithCompletion:(LabAudioCompletion)completion {
    [self recordWithReplay:YES completion:completion];
}

- (void)captureWAVWithCompletion:(LabCapturedAudioCompletion)completion {
    if (self.running) { completion(nil, @"Audio operation already running."); return; }
    self.returnValidatedData = YES;
    self.validatedData = nil;
    [self recordWithReplay:NO completion:^(NSString *status) {
        NSData *data = [status hasPrefix:@"Audio capture OK."] &&
                       [status hasSuffix:@"Local test file removed."] ? self.validatedData : nil;
        self.validatedData = nil;
        self.returnValidatedData = NO;
        completion(data, status);
    }];
}

- (void)recordWithReplay:(BOOL)replay completion:(LabAudioCompletion)completion {
    NSAssert([NSThread isMainThread], @"Audio controls use the main queue");
    if (self.running) { completion(@"Audio capture already running."); return; }
    if (![[LabAudioProbe permission] isEqual:@"granted"]) {
        completion(@"Microphone permission required. No recording started.");
        return;
    }
    NSFileManager *manager = [NSFileManager defaultManager];
    NSDictionary *attributes = AdaEnsurePrivateStorageDirectory() ? [manager attributesOfItemAtPath:AudioDirectory error:nil] : nil;
    if (![attributes[NSFileType] isEqual:NSFileTypeDirectory] ||
        [attributes[NSFileOwnerAccountID] unsignedIntegerValue] != 501 ||
        ([attributes[NSFilePosixPermissions] unsignedIntegerValue] & 0077) != 0) {
        completion(@"Private audio test directory unavailable. No recording started.");
        return;
    }
    self.running = YES;
    self.replayCapture = replay;
    self.completion = completion;
    NSString *name = [NSString stringWithFormat:@"audio-probe-%@.wav", [[NSUUID UUID] UUIDString]];
    self.file = [NSURL fileURLWithPath:[AudioDirectory stringByAppendingPathComponent:name]];
    if (![manager createFileAtPath:self.file.path contents:[NSData data]
                       attributes:@{NSFilePosixPermissions: @0600, NSFileProtectionKey: NSFileProtectionComplete}]) {
        [self finish:@"Audio private-file preparation failed."]; return;
    }
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSError *error = nil;
    if (![session setCategory:AVAudioSessionCategoryRecord error:&error] || ![session setActive:YES error:&error]) {
        [self finish:[NSString stringWithFormat:@"Audio session unavailable (code %ld).", (long)error.code]]; return;
    }
    NSDictionary *settings = @{AVFormatIDKey: @(kAudioFormatLinearPCM), AVSampleRateKey: @16000,
        AVNumberOfChannelsKey: @1, AVLinearPCMBitDepthKey: @16,
        AVLinearPCMIsFloatKey: @NO, AVLinearPCMIsBigEndianKey: @NO};
    self.recorder = [[AVAudioRecorder alloc] initWithURL:self.file settings:settings error:&error];
    self.recorder.delegate = self;
    if (!self.recorder || ![self.recorder prepareToRecord] ||
        ![manager setAttributes:@{NSFilePosixPermissions: @0600} ofItemAtPath:self.file.path error:nil] ||
        ![self.recorder recordForDuration:3.0]) {
        [self finish:[NSString stringWithFormat:@"Audio capture could not start (code %ld).", (long)error.code]]; return;
    }
    self.deadline = [NSTimer scheduledTimerWithTimeInterval:5.0 target:self selector:@selector(deadlineFired:)
                                                 userInfo:nil repeats:NO];
}

- (void)audioRecorderDidFinishRecording:(AVAudioRecorder *)recorder successfully:(BOOL)flag {
    if (!self.running || recorder != self.recorder) return;
    NSDictionary *attributes = [[NSFileManager defaultManager] attributesOfItemAtPath:self.file.path error:nil];
    NSUInteger size = [attributes[NSFileSize] unsignedIntegerValue];
    if (!flag || size < 44 || size > 131072) { [self finish:@"Audio capture failed or exceeded 128 KiB."]; return; }
    NSData *data = [NSData dataWithContentsOfURL:self.file];
    if (data.length != size || ![[data subdataWithRange:NSMakeRange(0, 4)] isEqualToData:[@"RIFF" dataUsingEncoding:NSASCIIStringEncoding]] ||
        ![[data subdataWithRange:NSMakeRange(8, 4)] isEqualToData:[@"WAVE" dataUsingEncoding:NSASCIIStringEncoding]]) {
        [self finish:@"Audio capture is not a bounded WAV file."]; return;
    }
    const uint8_t *bytes = data.bytes;
    BOOL formatOK = NO;
    uint32_t audioBytes = 0;
    for (NSUInteger offset = 12; offset + 8 <= size;) {
        uint32_t length = LE32(bytes + offset + 4);
        if (length > size - offset - 8) { [self finish:@"Audio WAV chunk bounds invalid."]; return; }
        NSData *kind = [data subdataWithRange:NSMakeRange(offset, 4)];
        if ([kind isEqualToData:[@"fmt " dataUsingEncoding:NSASCIIStringEncoding]] && length >= 16) {
            const uint8_t *f = bytes + offset + 8;
            formatOK = LE16(f) == 1 && LE16(f + 2) == 1 && LE32(f + 4) == 16000 &&
                       LE32(f + 8) == 32000 && LE16(f + 12) == 2 && LE16(f + 14) == 16;
        }
        if ([kind isEqualToData:[@"data" dataUsingEncoding:NSASCIIStringEncoding]]) audioBytes = length;
        offset += 8 + (NSUInteger)length + (length & 1);
    }
    if (!formatOK || audioBytes < 80000 || audioBytes > 112000) {
        [self finish:@"Audio PCM format or duration outside the three-second trial."]; return;
    }
    NSString *report = [NSString stringWithFormat:@"PCM 16000 Hz, mono, 16 bit, %.2f s, %u data bytes.",
                        (double)audioBytes / 32000.0, (unsigned int)audioBytes];
    if (self.replayCapture) {
        self.recorder.delegate = nil;
        self.recorder = nil;
        self.captureReport = report;
        [self startPlayback:data];
    } else {
        if (self.returnValidatedData) self.validatedData = data;
        [self finish:[@"Audio capture OK. " stringByAppendingString:report]];
    }
}

static void Append16(NSMutableData *data, uint16_t number) {
    uint8_t bytes[] = {(uint8_t)number, (uint8_t)(number >> 8)};
    [data appendBytes:bytes length:2];
}
static void Append32(NSMutableData *data, uint32_t number) {
    uint8_t bytes[] = {(uint8_t)number, (uint8_t)(number >> 8), (uint8_t)(number >> 16), (uint8_t)(number >> 24)};
    [data appendBytes:bytes length:4];
}
static void AppendKind(NSMutableData *data, NSString *kind) {
    [data appendData:[kind dataUsingEncoding:NSASCIIStringEncoding]];
}

+ (NSData *)oneSecondSilenceWAV {
    NSMutableData *data = [NSMutableData dataWithCapacity:32044];
    AppendKind(data, @"RIFF"); Append32(data, 32036); AppendKind(data, @"WAVE");
    AppendKind(data, @"fmt "); Append32(data, 16); Append16(data, 1); Append16(data, 1);
    Append32(data, 16000); Append32(data, 32000); Append16(data, 2); Append16(data, 16);
    AppendKind(data, @"data"); Append32(data, 32000);
    [data appendData:[NSMutableData dataWithLength:32000]];
    return data;
}

- (void)playSyntheticToneWithCompletion:(LabAudioCompletion)completion {
    NSAssert([NSThread isMainThread], @"Audio controls use the main queue");
    if (self.running) { completion(@"Audio operation already running."); return; }
    self.running = YES;
    self.replayCapture = NO;
    self.completion = completion;
    // One second of quiet synthetic PCM; this never uses the microphone or a file.
    NSMutableData *data = [NSMutableData dataWithCapacity:32044];
    AppendKind(data, @"RIFF"); Append32(data, 32036); AppendKind(data, @"WAVE");
    AppendKind(data, @"fmt "); Append32(data, 16); Append16(data, 1); Append16(data, 1);
    Append32(data, 16000); Append32(data, 32000); Append16(data, 2); Append16(data, 16);
    AppendKind(data, @"data"); Append32(data, 32000);
    for (int index = 0; index < 16000; index++) {
        int phase = index % 80;
        int sample = phase < 40 ? -2000 + phase * 100 : 6000 - phase * 100;
        int ramp = MIN(160, MIN(index, 15999 - index));
        Append16(data, (uint16_t)(int16_t)(sample * ramp / 160));
    }
    [self startPlayback:data];
}

- (void)startPlayback:(NSData *)data {
    [self.deadline invalidate];
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSError *error = nil;
    if (![session setCategory:AVAudioSessionCategoryPlayback error:&error] ||
        ![session setActive:YES error:&error]) {
        [self finish:[NSString stringWithFormat:@"Audio playback session unavailable (code %ld).", (long)error.code]];
        return;
    }
    self.player = [[AVAudioPlayer alloc] initWithData:data error:&error];
    self.player.delegate = self;
    self.player.numberOfLoops = 0;
    self.player.volume = self.replayCapture ? 1.0 : 0.25;
    if (!self.player || self.player.duration < 0.9 || self.player.duration > 3.5 ||
        ![self.player prepareToPlay] || ![self.player play]) {
        [self finish:[NSString stringWithFormat:@"Audio playback could not start (code %ld).", (long)error.code]];
        return;
    }
    self.playbackStarted = [NSDate date];
    self.deadline = [NSTimer scheduledTimerWithTimeInterval:self.player.duration + 2.0
        target:self selector:@selector(deadlineFired:) userInfo:nil repeats:NO];
}

- (void)audioPlayerDidFinishPlaying:(AVAudioPlayer *)player successfully:(BOOL)flag {
    if (!self.running || player != self.player) return;
    if (!flag || -self.playbackStarted.timeIntervalSinceNow < player.duration - 0.2) {
        [self finish:@"Audio playback failed or finished too early."]; return;
    }
    [self finish:self.replayCapture ? [@"Audio replay OK. " stringByAppendingString:self.captureReport] :
        @"Playback API OK. One-second synthetic tone completed. Audibility requires owner observation."];
}

- (void)audioPlayerDecodeErrorDidOccur:(AVAudioPlayer *)player error:(NSError *)error {
    if (self.running && player == self.player)
        [self finish:[NSString stringWithFormat:@"Audio playback decode failed (code %ld).", (long)error.code]];
}

- (void)audioRecorderEncodeErrorDidOccur:(AVAudioRecorder *)recorder error:(NSError *)error {
    if (self.running && recorder == self.recorder)
        [self finish:[NSString stringWithFormat:@"Audio encoder failed (code %ld).", (long)error.code]];
}

- (void)cancel { [self finish:@"Audio capture cancelled."]; }
@end
