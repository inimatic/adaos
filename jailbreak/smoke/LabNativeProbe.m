#import "LabNativeProbe.h"
#import "LabHTTPSProbe.h"
#import "LabSecureStorageProbe.h"
#import "LabSessionClient.h"
#import "LabAudioProbe.h"
#import <unistd.h>
#import <stdio.h>
#import <dispatch/dispatch.h>

static NSString *Outcome(NSString *status) {
    for (NSString *prefix in @[@"Session access OK", @"Test session paired", @"Local session removed",
                               @"HTTPS OK", @"Root API OK", @"Secure storage OK", @"Local metadata OK",
                               @"Microphone status OK", @"Audio capture OK", @"Playback API OK", @"Audio replay OK",
                               @"Voice API OK", @"Voice transcription API OK"])
        if ([status hasPrefix:prefix]) return @"ok";
    if ([status hasPrefix:@"Saved test session expired"]) return @"expired";
    if ([status hasPrefix:@"Microphone permission required"]) return @"permission-needed";
    if ([status hasPrefix:@"No readable saved session (code -25300)"]) return @"no-session";
    if ([status containsString:@"HTTP 401"] || [status containsString:@"HTTP 403"]) return @"denied";
    if ([status containsString:@"timeout"] || [status containsString:@"deadline"] ||
        [status containsString:@"code -1001"]) return @"timeout";
    return @"failure";
}

int LabNativeProbeMain(int argc, char *argv[]) {
    NSString *command = argc == 3 ? [NSString stringWithUTF8String:argv[2]] : @"";
    if (!command) command = @"";
    NSSet *commands = [NSSet setWithArray:@[@"status", @"website-https", @"root-ping",
                                          @"storage-check", @"session-check", @"session-pair", @"session-forget",
                                          @"microphone-status", @"microphone-record", @"speaker-check",
                                          @"voice-capabilities", @"voice-silence"]];
    BOOL recognized = [commands containsObject:command];
    if (!recognized) command = @"unknown"; // Never echo arbitrary arguments into a receipt.
    NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    NSString *bundle = [[NSBundle mainBundle] bundleIdentifier];
    CFAbsoluteTime started = CFAbsoluteTimeGetCurrent();
    __block NSString *result = nil;
    __block BOOL done = NO;
    LabSessionClient *session = nil;
    LabHTTPSProbe *probe = nil;
    LabAudioProbe *audio = nil;
    if (getuid() != 501 || geteuid() != 501) {
        result = @"Probe requires the ordinary mobile user (501). No operation performed.";
    } else if (!recognized) {
        result = @"Unknown probe command. No operation performed.";
    } else if (![bundle isEqual:@"com.inimatic.adaos.lab"] || !version.length) {
        result = @"App bundle metadata unavailable. No operation performed.";
    } else if ([command isEqual:@"status"]) {
        result = @"Local metadata OK. No network or Keychain operation.";
    } else if ([command isEqual:@"microphone-status"]) {
        result = @"Microphone status OK. No audio recorded.";
    } else {
        void (^completion)(NSString *) = ^(NSString *status) {
            result = status;
            done = YES;
        };
        if ([command hasPrefix:@"session-"] || [command hasPrefix:@"voice-"]) {
            session = [[LabSessionClient alloc] init];
            if ([command isEqual:@"voice-silence"]) {
                [session transcribeWAV:[LabAudioProbe oneSecondSilenceWAV] completion:^(NSString *status) {
                    if ([status hasPrefix:@"Transcription OK"] || [status hasPrefix:@"Transcription complete."])
                        completion(@"Voice transcription API OK. Synthetic silence only. No microphone capture.");
                    else completion(status);
                }];
            } else if ([command isEqual:@"voice-capabilities"]) [session checkVoiceWithCompletion:completion];
            else if ([command isEqual:@"session-check"]) [session checkWithCompletion:completion];
            else if ([command isEqual:@"session-pair"]) [session pairWithCompletion:completion];
            else [session forgetWithCompletion:completion];
        } else if ([command isEqual:@"microphone-record"] || [command isEqual:@"speaker-check"]) {
            audio = [[LabAudioProbe alloc] init];
            if ([command isEqual:@"speaker-check"]) [audio playSyntheticToneWithCompletion:completion];
            else [audio recordShortSampleWithCompletion:completion];
        } else if ([command isEqual:@"storage-check"]) {
            dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
                NSString *status = [LabSecureStorageProbe checkRoundTrip];
                dispatch_async(dispatch_get_main_queue(), ^{ completion(status); });
            });
        } else {
            probe = [[LabHTTPSProbe alloc] init];
            if ([command isEqual:@"root-ping"]) [probe startRootPingWithCompletion:completion];
            else [probe startWithCompletion:completion];
        }
        NSDate *end = [NSDate dateWithTimeIntervalSinceNow:40];
        while (!done && end.timeIntervalSinceNow > 0) {
            BOOL ran = [[NSRunLoop mainRunLoop] runMode:NSDefaultRunLoopMode
                                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
            if (!ran) [NSThread sleepForTimeInterval:0.01];
        }
        if (!done) {
            [session cancel];
            [probe cancel];
            [audio cancel];
            result = @"Probe deadline exceeded. A sent claim may have reached the server.";
        }
    }
    if (![result isKindOfClass:[NSString class]] || result.length > 512)
        result = @"Unexpected probe result withheld.";
    NSString *outcome = Outcome(result);
    NSMutableDictionary *receipt = [@{@"schema": @"adaos.lab.native_probe.v1",
        @"command": command, @"version": version ?: @"unknown", @"uid": @(getuid()),
        @"outcome": outcome, @"status": result,
        @"duration_ms": @((CFAbsoluteTimeGetCurrent() - started) * 1000.0),
        @"credentials_in_output": @NO} mutableCopy];
    if ([command isEqual:@"status"] && [outcome isEqual:@"ok"]) {
        NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
        receipt[@"saved_taps"] = @([defaults integerForKey:@"savedTaps"]);
        receipt[@"ui_launch_count"] = @([defaults integerForKey:@"launchCount"]);
        NSString *audioResult = [defaults stringForKey:@"lastAudioResult"];
        if ([@[@"capture-ok", @"replay-ok", @"failed"] containsObject:audioResult]) {
            receipt[@"last_audio_result"] = audioResult;
            receipt[@"last_audio_completed_at"] = @([defaults doubleForKey:@"lastAudioCompletedAt"]);
        }
        NSString *voiceResult = [defaults stringForKey:@"lastVoiceResult"];
        if ([@[@"recognized", @"empty", @"failed"] containsObject:voiceResult]) {
            receipt[@"last_voice_result"] = voiceResult;
            receipt[@"last_voice_completed_at"] = @([defaults doubleForKey:@"lastVoiceCompletedAt"]);
        }
        NSString *labResult = [defaults stringForKey:@"lastVoiceLabResult"];
        if ([@[@"running", @"completed", @"failed", @"cancelled"] containsObject:labResult]) {
            receipt[@"voice_lab_result"] = labResult;
            receipt[@"voice_lab_completed_at"] = @([defaults doubleForKey:@"lastVoiceLabCompletedAt"]);
            receipt[@"voice_lab_callback_count"] = @([defaults integerForKey:@"voiceLabCallbackCount"]);
            receipt[@"voice_lab_stopped"] = @([defaults boolForKey:@"voiceLabStopped"]);
        }
    }
    if ([command hasPrefix:@"microphone-"]) receipt[@"microphone_permission"] = [LabAudioProbe permission];
    NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:nil];
    if (!data || fwrite(data.bytes, 1, data.length, stdout) != data.length) return 74;
    fputc('\n', stdout);
    return [outcome isEqual:@"ok"] ? 0 : 1;
}
