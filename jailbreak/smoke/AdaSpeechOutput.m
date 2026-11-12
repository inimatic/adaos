#import "AdaSpeechOutput.h"
#import <AVFoundation/AVFoundation.h>

static NSString *const ReportKey = @"adaos.ios.speech-output.v1";
@interface AdaSpeechOutput () <AVSpeechSynthesizerDelegate>
@property(nonatomic, readwrite) BOOL busy;
@property(nonatomic, readwrite, copy) NSString *state;
@property(nonatomic, strong) AVSpeechSynthesizer *synth;
@property(nonatomic, copy) NSString *language;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) NSUInteger callbacks;
@property(nonatomic) NSUInteger characters;
@property(nonatomic) NSInteger errorCode;
@end
@implementation AdaSpeechOutput
- (instancetype)init { self = [super init]; if (self) self.state = @"idle"; return self; }
+ (NSDictionary *)report {
    id stored = [[NSUserDefaults standardUserDefaults] objectForKey:ReportKey];
    if (![stored isKindOfClass:[NSDictionary class]]) return @{@"state": @"absent"};
    NSMutableDictionary *safe = [NSMutableDictionary dictionary];
    for (NSString *key in @[@"callbacks", @"rendered_characters", @"output_volume", @"error_code", @"updated_at"])
        if ([stored[key] isKindOfClass:[NSNumber class]]) safe[key] = stored[key];
    if ([@[@"queued", @"started", @"completed", @"cancelled", @"unavailable", @"audio-session-error", @"start-timeout", @"playback-timeout"] containsObject:stored[@"state"]]) safe[@"state"] = stored[@"state"];
    if ([@[@"ru-RU", @"en-US"] containsObject:stored[@"language"]]) safe[@"language"] = stored[@"language"];
    if ([@[@"speaker", @"headphones", @"bluetooth", @"airplay", @"other", @"none"] containsObject:stored[@"route"]]) safe[@"route"] = stored[@"route"];
    return safe;
}
- (void)save:(NSString *)state {
    self.state = state;
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSString *port = session.currentRoute.outputs.firstObject.portType;
    NSString *route = !port ? @"none" : [port isEqual:AVAudioSessionPortBuiltInSpeaker] ? @"speaker" :
        [port isEqual:AVAudioSessionPortHeadphones] ? @"headphones" : [port isEqual:AVAudioSessionPortAirPlay] ? @"airplay" :
        [port hasPrefix:@"Bluetooth"] ? @"bluetooth" : @"other";
    [[NSUserDefaults standardUserDefaults] setObject:@{@"state": state, @"language": self.language ?: @"en-US",
        @"callbacks": @(self.callbacks), @"rendered_characters": @(self.characters), @"error_code": @(self.errorCode),
        @"route": route, @"output_volume": @(session.outputVolume), @"updated_at": @([NSDate date].timeIntervalSince1970)} forKey:ReportKey];
    [[NSUserDefaults standardUserDefaults] synchronize];
    if (self.changed) self.changed();
}
- (void)finish:(NSString *)state {
    self.busy = NO; self.generation++;
    self.synth.delegate = nil; [self.synth stopSpeakingAtBoundary:AVSpeechBoundaryImmediate]; self.synth = nil;
    [[AVAudioSession sharedInstance] setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
    [self save:state];
}
- (void)speak:(NSString *)text language:(NSString *)language {
    NSAssert([NSThread isMainThread], @"Speech uses the main queue");
    [self cancel];
    if (!text.length || text.length > 2000) return;
    self.language = [language isEqual:@"ru-RU"] ? @"ru-RU" : @"en-US";
    self.callbacks = 0; self.characters = 0; self.errorCode = 0;
    AVSpeechSynthesisVoice *voice = [AVSpeechSynthesisVoice voiceWithLanguage:self.language];
    if (!voice) { [self save:@"unavailable"]; return; }
    AVAudioSession *session = [AVAudioSession sharedInstance]; NSError *error = nil;
    if (![session setCategory:AVAudioSessionCategoryPlayback error:&error] ||
        ![session setMode:AVAudioSessionModeDefault error:&error] || ![session setActive:YES error:&error]) {
        self.errorCode = error.code; [self finish:@"audio-session-error"]; return;
    }
    self.busy = YES; NSUInteger generation = ++self.generation;
    /* A fresh instance avoids retaining a stopped/stalled utterance on old iOS. */
    self.synth = [[AVSpeechSynthesizer alloc] init]; self.synth.delegate = self;
    AVSpeechUtterance *utterance = [[AVSpeechUtterance alloc] initWithString:text];
    utterance.voice = voice; utterance.rate = AVSpeechUtteranceDefaultSpeechRate; utterance.volume = 1;
    [self save:@"queued"]; [self.synth speakUtterance:utterance];
    __weak AdaSpeechOutput *weakSelf = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (weakSelf.busy && generation == weakSelf.generation && [weakSelf.state isEqual:@"queued"]) [weakSelf finish:@"start-timeout"];
    });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 120 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (weakSelf.busy && generation == weakSelf.generation) [weakSelf finish:@"playback-timeout"];
    });
}
- (void)speechSynthesizer:(AVSpeechSynthesizer *)synth didStartSpeechUtterance:(AVSpeechUtterance *)utterance {
    (void)utterance; if (synth != self.synth || !self.busy) return; self.callbacks++; [self save:@"started"];
}
- (void)speechSynthesizer:(AVSpeechSynthesizer *)synth willSpeakRangeOfSpeechString:(NSRange)range utterance:(AVSpeechUtterance *)utterance {
    (void)utterance; if (synth != self.synth || !self.busy) return;
    self.callbacks++; self.characters = MAX(self.characters, NSMaxRange(range)); [self save:@"started"];
}
- (void)speechSynthesizer:(AVSpeechSynthesizer *)synth didFinishSpeechUtterance:(AVSpeechUtterance *)utterance {
    (void)utterance; if (synth != self.synth || !self.busy) return; self.callbacks++; [self finish:@"completed"];
}
- (void)speechSynthesizer:(AVSpeechSynthesizer *)synth didCancelSpeechUtterance:(AVSpeechUtterance *)utterance {
    (void)utterance; if (synth != self.synth || !self.busy) return; self.callbacks++; [self finish:@"cancelled"];
}
- (void)cancel { if (self.busy) [self finish:@"cancelled"]; }
- (void)dealloc { self.synth.delegate = nil; [self.synth stopSpeakingAtBoundary:AVSpeechBoundaryImmediate]; }
@end
