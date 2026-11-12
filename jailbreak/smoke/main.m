#import <UIKit/UIKit.h>
#import "LabHTTPSProbe.h"
#import "LabSecureStorageProbe.h"
#import "LabSessionClient.h"
#import "LabNativeProbe.h"
#import "LabAudioProbe.h"
#import "AdaOnboardingViewController.h"
#import "AdaConversationViewController.h"
#import "AdaOnboardingStore.h"
#import "AdaSubnetSession.h"
#import <math.h>

@interface LabViewController : UIViewController
@property(nonatomic, strong) UILabel *heading;
@property(nonatomic, strong) UILabel *details;
@property(nonatomic, strong) UILabel *counter;
@property(nonatomic, strong) UIButton *increment;
@property(nonatomic, strong) UILabel *activity;
@property(nonatomic, strong) UIScrollView *scroll;
@property(nonatomic, strong) UIButton *networkCheck;
@property(nonatomic, strong) UILabel *networkStatus;
@property(nonatomic, strong) LabHTTPSProbe *probe;
@property(nonatomic, strong) UIButton *apiCheck;
@property(nonatomic, strong) UILabel *apiStatus;
@property(nonatomic, strong) LabHTTPSProbe *apiProbe;
@property(nonatomic, strong) UIButton *storageCheck;
@property(nonatomic, strong) UILabel *storageStatus;
@property(nonatomic, strong) LabSessionClient *sessionClient;
@property(nonatomic, strong) UIButton *pairSession;
@property(nonatomic, strong) UIButton *sessionCheck;
@property(nonatomic, strong) UIButton *forgetSession;
@property(nonatomic, strong) UILabel *sessionStatus;
@property(nonatomic, strong) LabAudioProbe *audioProbe;
@property(nonatomic, strong) UIButton *microphonePermission;
@property(nonatomic, strong) UILabel *microphonePermissionStatus;
@property(nonatomic, strong) UIButton *recordSample;
@property(nonatomic, strong) UIButton *replaySample;
@property(nonatomic, strong) UIButton *transcribeSample;
@property(nonatomic, strong) UIButton *cancelVoiceButton;
@property(nonatomic, strong) UILabel *voiceStatus;
@property(nonatomic) NSUInteger voiceGeneration;
@property(nonatomic) BOOL voiceActive;
@property(nonatomic) BOOL voiceLabActive;
@property(nonatomic, strong) UILabel *audioStatus;
@property(nonatomic) BOOL permissionRequestRunning;
- (void)refresh;
@end

@implementation LabViewController
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithWhite:0.96 alpha:1.0];
    self.scroll = [[UIScrollView alloc] initWithFrame:self.view.bounds];
    [self.view addSubview:self.scroll];
    self.heading = [[UILabel alloc] init];
    self.heading.text = @"AdaOS Lab";
    self.heading.font = [UIFont boldSystemFontOfSize:32];
    self.details = [[UILabel alloc] init];
    self.details.numberOfLines = 0;
    self.details.font = [UIFont systemFontOfSize:18];
    NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    self.details.text = [NSString stringWithFormat:@"Native lab client %@\niOS %@\nOffline counter, HTTPS and test session", version, [[UIDevice currentDevice] systemVersion]];
    self.counter = [[UILabel alloc] init];
    self.counter.font = [UIFont systemFontOfSize:24];
    self.increment = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.increment setTitle:@"Save one more tap" forState:UIControlStateNormal];
    self.increment.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.increment addTarget:self action:@selector(saveTap) forControlEvents:UIControlEventTouchUpInside];
    self.activity = [[UILabel alloc] init];
    self.activity.numberOfLines = 0;
    self.activity.font = [UIFont systemFontOfSize:16];
    self.networkCheck = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.networkCheck setTitle:@"Check inimatic.com" forState:UIControlStateNormal];
    self.networkCheck.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.networkCheck addTarget:self action:@selector(checkNetwork) forControlEvents:UIControlEventTouchUpInside];
    self.networkStatus = [[UILabel alloc] init];
    self.networkStatus.numberOfLines = 0;
    self.networkStatus.font = [UIFont systemFontOfSize:18];
    self.networkStatus.text = @"Not checked. Use Wi-Fi and tap to check HTTPS.";
    self.probe = [[LabHTTPSProbe alloc] init];
    self.apiCheck = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.apiCheck setTitle:@"Check Root API" forState:UIControlStateNormal];
    self.apiCheck.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.apiCheck addTarget:self action:@selector(checkAPI) forControlEvents:UIControlEventTouchUpInside];
    self.apiStatus = [[UILabel alloc] init];
    self.apiStatus.numberOfLines = 0;
    self.apiStatus.font = [UIFont systemFontOfSize:18];
    self.apiStatus.text = @"Not checked. Public read-only JSON at api.inimatic.com. No account needed.";
    self.apiProbe = [[LabHTTPSProbe alloc] init];
    self.storageCheck = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.storageCheck setTitle:@"Check secure storage" forState:UIControlStateNormal];
    self.storageCheck.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.storageCheck addTarget:self action:@selector(checkStorage) forControlEvents:UIControlEventTouchUpInside];
    self.storageStatus = [[UILabel alloc] init];
    self.storageStatus.numberOfLines = 0;
    self.storageStatus.font = [UIFont systemFontOfSize:18];
    self.storageStatus.text = @"Not checked. Save, read and remove a temporary test value in iOS Keychain.";
    self.sessionClient = [[LabSessionClient alloc] init];
    self.pairSession = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.pairSession setTitle:@"Pair test session" forState:UIControlStateNormal];
    self.pairSession.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.pairSession addTarget:self action:@selector(pairTestSession) forControlEvents:UIControlEventTouchUpInside];
    self.sessionCheck = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.sessionCheck setTitle:@"Check session access" forState:UIControlStateNormal];
    self.sessionCheck.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.sessionCheck addTarget:self action:@selector(checkTestSession) forControlEvents:UIControlEventTouchUpInside];
    self.forgetSession = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.forgetSession setTitle:@"Forget test session" forState:UIControlStateNormal];
    self.forgetSession.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.forgetSession addTarget:self action:@selector(forgetTestSession) forControlEvents:UIControlEventTouchUpInside];
    self.sessionStatus = [[UILabel alloc] init];
    self.sessionStatus.numberOfLines = 0;
    self.sessionStatus.font = [UIFont systemFontOfSize:18];
    self.sessionStatus.text = @"No session check yet. Pair with the prepared test invitation, then check access. No account password needed.";
    self.audioProbe = [[LabAudioProbe alloc] init];
    self.microphonePermission = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.microphonePermission setTitle:@"Allow microphone for local test" forState:UIControlStateNormal];
    self.microphonePermission.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.microphonePermission addTarget:self action:@selector(requestMicrophonePermission) forControlEvents:UIControlEventTouchUpInside];
    self.microphonePermissionStatus = [[UILabel alloc] init];
    self.microphonePermissionStatus.numberOfLines = 0;
    self.microphonePermissionStatus.font = [UIFont systemFontOfSize:18];
    self.microphonePermissionStatus.text = [NSString stringWithFormat:@"Microphone permission: %@. No audio recorded.", [LabAudioProbe permission]];
    self.recordSample = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.recordSample setTitle:@"Record a 3-second local sample" forState:UIControlStateNormal];
    self.recordSample.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.recordSample addTarget:self action:@selector(recordLocalSample) forControlEvents:UIControlEventTouchUpInside];
    self.audioStatus = [[UILabel alloc] init];
    self.audioStatus.numberOfLines = 0;
    self.audioStatus.font = [UIFont systemFontOfSize:18];
    self.audioStatus.text = @"Local PCM format test. The test file is deleted after inspection.";
    self.replaySample = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.replaySample setTitle:@"Record and replay locally" forState:UIControlStateNormal];
    self.replaySample.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.replaySample addTarget:self action:@selector(recordAndReplay) forControlEvents:UIControlEventTouchUpInside];
    self.transcribeSample = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.transcribeSample setTitle:@"Record and transcribe on AdaOS" forState:UIControlStateNormal];
    self.transcribeSample.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    [self.transcribeSample addTarget:self action:@selector(recordAndTranscribe) forControlEvents:UIControlEventTouchUpInside];
    self.cancelVoiceButton = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.cancelVoiceButton setTitle:@"Cancel voice operation" forState:UIControlStateNormal];
    self.cancelVoiceButton.titleLabel.font = [UIFont boldSystemFontOfSize:22];
    self.cancelVoiceButton.enabled = NO;
    [self.cancelVoiceButton addTarget:self action:@selector(cancelVoice) forControlEvents:UIControlEventTouchUpInside];
    self.voiceStatus = [[UILabel alloc] init];
    self.voiceStatus.numberOfLines = 0;
    self.voiceStatus.font = [UIFont systemFontOfSize:18];
    self.voiceStatus.text = @"Three seconds of speech are sent to your AdaOS node. The transcript appears here; the local recording is deleted.";
    for (UIView *item in @[self.heading, self.details, self.counter, self.increment, self.activity,
                           self.networkCheck, self.networkStatus, self.apiCheck, self.apiStatus,
                           self.storageCheck, self.storageStatus, self.pairSession, self.sessionCheck,
                           self.forgetSession, self.sessionStatus, self.microphonePermission,
                           self.microphonePermissionStatus, self.recordSample, self.replaySample, self.audioStatus,
                           self.transcribeSample, self.cancelVoiceButton, self.voiceStatus]) {
        [self.scroll addSubview:item];
    }
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(becameActive:)
        name:UIApplicationDidBecomeActiveNotification object:nil];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(leftForeground:)
        name:UIApplicationWillResignActiveNotification object:nil];
    [self refresh];
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGFloat width = CGRectGetWidth(self.view.bounds);
    self.scroll.frame = self.view.bounds;
    self.scroll.contentSize = CGSizeMake(width, 2560);
    CGFloat left = MAX(24.0, (width - 620.0) / 2.0);
    CGFloat contentWidth = width - 2.0 * left;
    self.heading.frame = CGRectMake(left, 55, contentWidth, 50);
    self.details.frame = CGRectMake(left, 118, contentWidth, 110);
    self.counter.frame = CGRectMake(left, 240, contentWidth, 45);
    self.increment.frame = CGRectMake(left, 295, contentWidth, 60);
    self.activity.frame = CGRectMake(left, 370, contentWidth, 110);
    self.networkCheck.frame = CGRectMake(left, 502, contentWidth, 56);
    self.networkStatus.frame = CGRectMake(left, 574, contentWidth, 150);
    self.apiCheck.frame = CGRectMake(left, 746, contentWidth, 56);
    self.apiStatus.frame = CGRectMake(left, 818, contentWidth, 155);
    self.storageCheck.frame = CGRectMake(left, 995, contentWidth, 56);
    self.storageStatus.frame = CGRectMake(left, 1073, contentWidth, 155);
    self.pairSession.frame = CGRectMake(left, 1240, contentWidth, 56);
    self.sessionCheck.frame = CGRectMake(left, 1310, contentWidth, 56);
    self.forgetSession.frame = CGRectMake(left, 1380, contentWidth, 56);
    self.sessionStatus.frame = CGRectMake(left, 1450, contentWidth, 210);
    self.microphonePermission.frame = CGRectMake(left, 1680, contentWidth, 50);
    self.microphonePermissionStatus.frame = CGRectMake(left, 1742, contentWidth, 80);
    self.recordSample.frame = CGRectMake(left, 1835, contentWidth, 50);
    self.replaySample.frame = CGRectMake(left, 1900, contentWidth, 50);
    self.audioStatus.frame = CGRectMake(left, 1970, contentWidth, 160);
    self.transcribeSample.frame = CGRectMake(left, 2150, contentWidth, 60);
    self.cancelVoiceButton.frame = CGRectMake(left, 2220, contentWidth, 56);
    self.voiceStatus.frame = CGRectMake(left, 2300, contentWidth, 210);
}
- (void)requestMicrophonePermission {
    if (self.permissionRequestRunning) return;
    self.permissionRequestRunning = YES;
    self.microphonePermission.enabled = NO;
    __weak LabViewController *weakSelf = self;
    [LabAudioProbe requestPermissionWithCompletion:^(NSString *status) {
        LabViewController *controller = weakSelf;
        controller.permissionRequestRunning = NO;
        controller.microphonePermission.enabled = YES;
        controller.microphonePermissionStatus.text = status;
    }];
}
- (void)checkPendingMicrophonePermission {
    // Only a private, short-lived, fixed lab job can ask for the system prompt.
    // URLs merely open this UI and never select an operation or record audio.
    NSString *path = @"/var/mobile/Library/Application Support/AdaOSLab/permission-request.json";
    NSFileManager *manager = [NSFileManager defaultManager];
    NSDictionary *attributes = [manager attributesOfItemAtPath:path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular] || [attributes[NSFileSize] unsignedLongLongValue] > 512 ||
        [attributes[NSFileOwnerAccountID] unsignedIntegerValue] != 501 ||
        ([attributes[NSFilePosixPermissions] unsignedIntegerValue] & 0077) != 0) return;
    NSData *data = [NSData dataWithContentsOfFile:path];
    id job = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![job isKindOfClass:[NSDictionary class]] || [job count] != 2 ||
        ![job[@"operation"] isEqual:@"microphone-permission"] ||
        ![job[@"expires_at"] isKindOfClass:[NSNumber class]]) return;
    double remaining = [job[@"expires_at"] doubleValue] - [[NSDate date] timeIntervalSince1970];
    if (!isfinite(remaining) || remaining > 900) return;
    if (![manager removeItemAtPath:path error:nil]) return;
    if (remaining > 0) [self requestMicrophonePermission];
}
- (void)recordLocalSample {
    [self runLocalAudioWithReplay:NO];
}
- (void)recordAndReplay {
    [self runLocalAudioWithReplay:YES];
}
- (void)runLocalAudioWithReplay:(BOOL)replay {
    if (self.voiceActive) [self cancelVoice];
    self.voiceGeneration++;
    self.transcribeSample.enabled = YES;
    [self.sessionClient cancel];
    if (self.audioProbe.isRunning) { [self.audioProbe cancel]; return; }
    self.recordSample.enabled = NO;
    self.replaySample.enabled = NO;
    self.audioStatus.text = replay ? @"Speak now for three seconds. Local replay follows, then the file is deleted." :
                                    @"Checking three seconds of local microphone capture...";
    __weak LabViewController *weakSelf = self;
    LabAudioCompletion completion = ^(NSString *status) {
        LabViewController *controller = weakSelf;
        controller.audioStatus.text = status;
        controller.recordSample.enabled = YES;
        controller.replaySample.enabled = YES;
        NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
        NSString *result = [status hasPrefix:@"Audio replay OK"] ? @"replay-ok" :
                           [status hasPrefix:@"Audio capture OK"] ? @"capture-ok" : @"failed";
        [defaults setObject:result forKey:@"lastAudioResult"];
        [defaults setDouble:[[NSDate date] timeIntervalSince1970] forKey:@"lastAudioCompletedAt"];
        [defaults synchronize];
    };
    if (replay) [self.audioProbe recordShortSampleAndReplayWithCompletion:completion];
    else [self.audioProbe recordShortSampleWithCompletion:completion];
}
- (void)recordAndTranscribe {
    [self cancelVoice];
    [self.audioProbe cancel];
    NSUInteger generation = ++self.voiceGeneration;
    self.voiceActive = YES;
    self.transcribeSample.enabled = NO;
    self.cancelVoiceButton.enabled = YES;
    self.voiceStatus.text = @"Checking the selected AdaOS voice connection...";
    __weak LabViewController *weakSelf = self;
    [self.sessionClient checkVoiceWithCompletion:^(NSString *status) {
        LabViewController *controller = weakSelf;
        if (!controller || generation != controller.voiceGeneration) return;
        if (![status hasPrefix:@"Voice API OK"] || [UIApplication sharedApplication].applicationState != UIApplicationStateActive) {
            controller.voiceStatus.text = status;
            [controller finishVoiceOperation:status recordResult:NO];
            return;
        }
        controller.voiceStatus.text = @"Speak now for three seconds. Sending to your AdaOS node follows.";
        [controller.audioProbe captureWAVWithCompletion:^(NSData *wav, NSString *captureStatus) {
            LabViewController *active = weakSelf;
            if (!active || generation != active.voiceGeneration) return;
            if (!wav || [UIApplication sharedApplication].applicationState != UIApplicationStateActive) {
                active.voiceStatus.text = captureStatus;
                [active finishVoiceOperation:captureStatus recordResult:NO];
                return;
            }
            active.voiceStatus.text = @"Recognizing on AdaOS. The local recording has been deleted...";
            [active.sessionClient transcribeWAV:wav completion:^(NSString *result) {
                LabViewController *finished = weakSelf;
                if (!finished || generation != finished.voiceGeneration) return;
                [finished finishVoiceOperation:result recordResult:YES];
            }];
        }];
    }];
}
- (void)finishVoiceOperation:(NSString *)result recordResult:(BOOL)record {
    self.voiceStatus.text = result;
    self.voiceActive = NO;
    self.transcribeSample.enabled = YES;
    self.cancelVoiceButton.enabled = NO;
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    BOOL recognized = [result hasPrefix:@"Transcription OK."] || [result hasPrefix:@"Transcription complete."];
    if (self.voiceLabActive) {
        [defaults setObject:recognized ? @"completed" : @"failed" forKey:@"lastVoiceLabResult"];
        [defaults setInteger:[defaults integerForKey:@"voiceLabCallbackCount"] + 1 forKey:@"voiceLabCallbackCount"];
        [defaults setDouble:[[NSDate date] timeIntervalSince1970] forKey:@"lastVoiceLabCompletedAt"];
        self.voiceLabActive = NO;
    } else if (record) {
        NSString *code = [result hasPrefix:@"Transcription OK."] ? @"recognized" :
                         [result hasPrefix:@"Transcription complete."] ? @"empty" : @"failed";
        [defaults setObject:code forKey:@"lastVoiceResult"];
        [defaults setDouble:[[NSDate date] timeIntervalSince1970] forKey:@"lastVoiceCompletedAt"];
    }
    [defaults synchronize];
}
- (void)cancelVoice {
    BOOL wasActive = self.voiceActive;
    self.voiceGeneration++;
    self.voiceActive = NO;
    [self.sessionClient cancel];
    [self.audioProbe cancel];
    self.transcribeSample.enabled = YES;
    self.cancelVoiceButton.enabled = NO;
    if (wasActive) self.voiceStatus.text = @"Voice operation cancelled. No automatic retry.";
    if (self.voiceLabActive) {
        NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
        [defaults setObject:@"cancelled" forKey:@"lastVoiceLabResult"];
        [defaults setBool:!self.sessionClient.isRunning && !self.audioProbe.isRunning forKey:@"voiceLabStopped"];
        [defaults setDouble:[[NSDate date] timeIntervalSince1970] forKey:@"lastVoiceLabCompletedAt"];
        [defaults synchronize];
        self.voiceLabActive = NO;
    }
}
- (void)checkPendingVoiceLab {
    // Private one-use fixture exercises the real foreground lifecycle with
    // generated silence only. URLs never select a job or start the microphone.
    if (self.voiceActive || self.audioProbe.isRunning || self.sessionClient.isRunning ||
        [UIApplication sharedApplication].applicationState != UIApplicationStateActive) return;
    NSString *path = @"/var/mobile/Library/Application Support/AdaOSLab/voice-lifecycle-test.json";
    NSFileManager *manager = [NSFileManager defaultManager];
    NSDictionary *attributes = [manager attributesOfItemAtPath:path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular] || [attributes[NSFileSize] unsignedLongLongValue] > 512 ||
        [attributes[NSFileOwnerAccountID] unsignedIntegerValue] != 501 ||
        ([attributes[NSFilePosixPermissions] unsignedIntegerValue] & 0077) != 0) return;
    NSData *data = [NSData dataWithContentsOfFile:path];
    id job = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![job isKindOfClass:[NSDictionary class]] || [job count] != 2 ||
        ![job[@"operation"] isEqual:@"voice-silence"] || ![job[@"expires_at"] isKindOfClass:[NSNumber class]]) return;
    double remaining = [job[@"expires_at"] doubleValue] - [[NSDate date] timeIntervalSince1970];
    if (!isfinite(remaining) || remaining > 120 || ![manager removeItemAtPath:path error:nil]) return;
    if (remaining <= 0) return;
    NSUInteger generation = ++self.voiceGeneration;
    self.voiceActive = YES;
    self.voiceLabActive = YES;
    self.transcribeSample.enabled = NO;
    self.cancelVoiceButton.enabled = YES;
    self.voiceStatus.text = @"Synthetic voice lifecycle check. No microphone capture.";
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults setObject:@"running" forKey:@"lastVoiceLabResult"];
    [defaults setInteger:0 forKey:@"voiceLabCallbackCount"];
    [defaults setBool:NO forKey:@"voiceLabStopped"];
    [defaults synchronize];
    __weak LabViewController *weakSelf = self;
    [self.sessionClient transcribeWAV:[LabAudioProbe oneSecondSilenceWAV] completion:^(NSString *status) {
        LabViewController *controller = weakSelf;
        if (!controller || generation != controller.voiceGeneration) return;
        [controller finishVoiceOperation:status recordResult:NO];
    }];
}
- (void)checkNetwork {
    if (self.voiceActive) [self cancelVoice];
    self.voiceGeneration++;
    self.transcribeSample.enabled = YES;
    [self.audioProbe cancel];
    [self.sessionClient cancel];
    if (self.probe.isRunning) {
        [self.probe cancel];
        return;
    }
    [self.apiProbe cancel];
    self.networkStatus.text = @"Connecting securely to inimatic.com...";
    [self.networkCheck setTitle:@"Cancel check" forState:UIControlStateNormal];
    __weak LabViewController *weakSelf = self;
    [self.probe startWithCompletion:^(NSString *status) {
        LabViewController *controller = weakSelf;
        if (!controller) {
            return;
        }
        controller.networkStatus.text = status;
        [controller.networkCheck setTitle:@"Check inimatic.com" forState:UIControlStateNormal];
    }];
}
- (void)checkAPI {
    if (self.voiceActive) [self cancelVoice];
    self.voiceGeneration++;
    self.transcribeSample.enabled = YES;
    [self.audioProbe cancel];
    [self.sessionClient cancel];
    if (self.apiProbe.isRunning) {
        [self.apiProbe cancel];
        return;
    }
    [self.probe cancel];
    self.apiStatus.text = @"Reading JSON securely from api.inimatic.com...";
    [self.apiCheck setTitle:@"Cancel API check" forState:UIControlStateNormal];
    __weak LabViewController *weakSelf = self;
    [self.apiProbe startRootPingWithCompletion:^(NSString *status) {
        LabViewController *controller = weakSelf;
        if (!controller) {
            return;
        }
        controller.apiStatus.text = status;
        [controller.apiCheck setTitle:@"Check Root API" forState:UIControlStateNormal];
    }];
}
- (void)checkStorage {
    if (!self.storageCheck.enabled) {
        return;
    }
    self.storageCheck.enabled = NO;
    self.storageStatus.text = @"Checking local secure storage...";
    __weak LabViewController *weakSelf = self;
    dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
        NSString *result = [LabSecureStorageProbe checkRoundTrip];
        dispatch_async(dispatch_get_main_queue(), ^{
            LabViewController *controller = weakSelf;
            controller.storageStatus.text = result;
            controller.storageCheck.enabled = YES;
        });
    });
}
- (void)pairTestSession { [self sessionAction:0]; }
- (void)checkTestSession { [self sessionAction:1]; }
- (void)forgetTestSession { [self sessionAction:2]; }
- (void)sessionAction:(NSInteger)action {
    if (self.voiceActive) [self cancelVoice];
    self.voiceGeneration++;
    self.transcribeSample.enabled = YES;
    [self.audioProbe cancel];
    if (self.sessionClient.isRunning && action != 2) {
        [self.sessionClient cancel];
        return;
    }
    [self.probe cancel];
    [self.apiProbe cancel];
    self.sessionStatus.text = @"Checking test session...";
    [self.pairSession setTitle:@"Cancel session request" forState:UIControlStateNormal];
    __weak LabViewController *weakSelf = self;
    LabSessionCompletion completion = ^(NSString *status) {
        LabViewController *controller = weakSelf;
        controller.sessionStatus.text = status;
        [controller.pairSession setTitle:@"Pair test session" forState:UIControlStateNormal];
    };
    if (action == 0) [self.sessionClient pairWithCompletion:completion];
    else if (action == 1) [self.sessionClient checkWithCompletion:completion];
    else [self.sessionClient forgetWithCompletion:completion];
}
- (void)leftForeground:(NSNotification *)notification {
    (void)notification;
    [self cancelVoice];
    [self.probe cancel];
    [self.apiProbe cancel];
    [self.sessionClient cancel];
    [self.audioProbe cancel];
}
- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    [self cancelVoice];
    [self.probe cancel];
    [self.apiProbe cancel];
    [self.sessionClient cancel];
    [self.audioProbe cancel];
}
- (void)saveTap {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults setInteger:[defaults integerForKey:@"savedTaps"] + 1 forKey:@"savedTaps"];
    [defaults synchronize];
    [self refresh];
}
- (void)becameActive:(NSNotification *)notification {
    (void)notification;
    [self refresh];
    [self checkPendingMicrophonePermission];
    [self checkPendingVoiceLab];
}
- (void)refresh {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    self.counter.text = [NSString stringWithFormat:@"Saved taps: %ld", (long)[defaults integerForKey:@"savedTaps"]];
    NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
    formatter.dateStyle = NSDateFormatterShortStyle;
    formatter.timeStyle = NSDateFormatterMediumStyle;
    self.activity.text = [NSString stringWithFormat:@"Application launches: %ld\nLast active: %@\nCounter must survive a full power cycle.",
        (long)[defaults integerForKey:@"launchCount"], [formatter stringFromDate:[NSDate date]]];
}
- (void)dealloc {
    [self.probe cancel];
    [self.apiProbe cancel];
    [self.sessionClient cancel];
    [self.audioProbe cancel];
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}
@end

@interface LabAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

@implementation LabAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    (void)application;
    (void)options;
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults setInteger:[defaults integerForKey:@"launchCount"] + 1 forKey:@"launchCount"];
    [defaults synchronize];
    self.window = [[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]];
    AdaOnboardingViewController *onboarding = [[AdaOnboardingViewController alloc] init];
    onboarding.labFactory = ^UIViewController *{ return [[LabViewController alloc] init]; };
    onboarding.connectionFactory = ^UIViewController *{ return [[AdaConversationViewController alloc] init]; };
    UINavigationController *navigation = [[UINavigationController alloc] initWithRootViewController:onboarding];
    NSInteger clientStorageStatus;
    if ([[AdaOnboardingStore alloc] init].state.step == AdaConnection && [AdaSubnetSession read:&clientStorageStatus])
        [navigation setViewControllers:@[onboarding, [[AdaConversationViewController alloc] init]] animated:NO];
    self.window.rootViewController = navigation;
    [self.window makeKeyAndVisible];
    return YES;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        if (argc >= 2 && [[NSString stringWithUTF8String:argv[1]] isEqualToString:@"--lab-probe"])
            return LabNativeProbeMain(argc, argv);
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([LabAppDelegate class]));
    }
}
