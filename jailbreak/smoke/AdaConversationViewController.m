#import "AdaConversationViewController.h"
#import "AdaSubnetClient.h"
#import "AdaOnboardingStore.h"
#import "LabAudioProbe.h"
#import "AdaClientUIProbe.h"
#import "AdaSpeechOutput.h"
#import "AdaLocalUIProbe.h"
#import "AdaVoiceActivation.h"
#import "AdaVoiceSetupStore.h"
#import "AdaVoiceUIProbe.h"
#import <AVFoundation/AVFoundation.h>
#import <CoreImage/CoreImage.h>

@interface AdaConversationViewController () <UITextViewDelegate>
@property(nonatomic, strong) AdaSubnetClient *client;
@property(nonatomic, strong) AdaClientUIProbe *clientProbe;
@property(nonatomic, strong) LabAudioProbe *audio;
@property(nonatomic, strong) AdaSpeechOutput *speech;
@property(nonatomic, strong) AdaVoiceActivation *activation;
@property(nonatomic, strong) UILabel *stateLabel;
@property(nonatomic, strong) UILabel *voiceStateLabel;
@property(nonatomic, strong) UIImageView *qr;
@property(nonatomic, strong) UITextView *history;
@property(nonatomic, strong) UITextView *editor;
@property(nonatomic, strong) UIButton *sendButton;
@property(nonatomic, strong) UIButton *voiceButton;
@property(nonatomic, strong) UIButton *ruButton;
@property(nonatomic, strong) UIButton *globalButton;
@property(nonatomic, strong) UIButton *cancelButton;
@property(nonatomic, strong) UIButton *reconnectButton;
@property(nonatomic, strong) UIButton *speechButton;
@property(nonatomic, strong) UIButton *stopButton;
@property(nonatomic, strong) UIButton *stopSpeechButton;
@property(nonatomic, strong) UIButton *listenButton;
@property(nonatomic, strong) UIScrollView *setupView;
@property(nonatomic, strong) NSMutableArray *setupRows;
@property(nonatomic, strong) AdaVoiceSetupStore *voiceSetup;
@property(nonatomic, strong) NSTimer *probeTimer;
@property(nonatomic) BOOL showingSetup;
@property(nonatomic, copy) NSString *setupSignature;
@property(nonatomic, copy) NSString *shownPairURL;
@property(nonatomic, copy) NSString *notice;
@property(nonatomic, strong) NSMutableSet *spoken;
@property(nonatomic) BOOL russian;
@property(nonatomic) BOOL recording;
@property(nonatomic) BOOL readReplies;
@property(nonatomic) BOOL visible;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) CGFloat keyboardHeight;
@end

@implementation AdaConversationViewController
- (NSString *)ru:(NSString *)ru en:(NSString *)en { return self.russian ? ru : en; }
- (UIButton *)button:(NSString *)title selector:(SEL)selector {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    button.titleLabel.font = [UIFont systemFontOfSize:18];
    button.titleLabel.numberOfLines = 0; button.titleLabel.textAlignment = NSTextAlignmentCenter;
    button.backgroundColor = [UIColor colorWithWhite:0.94 alpha:1]; button.layer.cornerRadius = 6;
    [button setTitle:title forState:UIControlStateNormal];
    [button addTarget:self action:selector forControlEvents:UIControlEventTouchUpInside];
    [self.view addSubview:button]; return button;
}
- (void)viewDidLoad {
    [super viewDidLoad];
    self.russian = [[AdaOnboardingStore alloc] init].state.russian;
    self.title = @"Conversational"; self.view.backgroundColor = [UIColor whiteColor];
    self.client = [[AdaSubnetClient alloc] init];
    self.client.diagnostics = [[[AdaOnboardingStore alloc] init] report];
    self.audio = [[LabAudioProbe alloc] init]; self.speech = [[AdaSpeechOutput alloc] init];
    self.spoken = [NSMutableSet set];
    self.readReplies = [[NSUserDefaults standardUserDefaults] boolForKey:@"adaos.ios.read-replies"];
    self.voiceSetup = [[AdaVoiceSetupStore alloc] init];
    self.showingSetup = ![self.voiceSetup.report[@"finished"] boolValue];
    [self.view addSubview:self.client.transport.view];
    self.stateLabel = [[UILabel alloc] init]; self.stateLabel.numberOfLines = 0;
    self.stateLabel.font = [UIFont systemFontOfSize:18]; [self.view addSubview:self.stateLabel];
    self.voiceStateLabel = [[UILabel alloc] init]; self.voiceStateLabel.numberOfLines = 2;
    self.voiceStateLabel.font = [UIFont boldSystemFontOfSize:20];
    self.voiceStateLabel.accessibilityTraits = UIAccessibilityTraitUpdatesFrequently;
    [self.view addSubview:self.voiceStateLabel];
    self.setupView = [[UIScrollView alloc] init]; self.setupView.alwaysBounceVertical = YES;
    [self.view addSubview:self.setupView];
    self.qr = [[UIImageView alloc] init]; self.qr.contentMode = UIViewContentModeScaleAspectFit;
    self.qr.layer.magnificationFilter = kCAFilterNearest; [self.view addSubview:self.qr];
    self.history = [[UITextView alloc] init]; self.history.editable = NO;
    self.history.font = [UIFont systemFontOfSize:20]; [self.view addSubview:self.history];
    self.editor = [[UITextView alloc] init]; self.editor.delegate = self; self.editor.font = [UIFont systemFontOfSize:20];
    self.editor.layer.borderWidth = 1; self.editor.layer.borderColor = [UIColor lightGrayColor].CGColor;
    self.editor.layer.cornerRadius = 6;
    self.editor.accessibilityLabel = [self ru:@"Сообщение" en:@"Message"];
    UIToolbar *keyboardToolbar = [[UIToolbar alloc] initWithFrame:CGRectMake(0, 0, 320, 44)];
    keyboardToolbar.items = @[[[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemFlexibleSpace target:nil action:nil],
        [[UIBarButtonItem alloc] initWithTitle:[self ru:@"Готово" en:@"Done"] style:UIBarButtonItemStyleDone target:self action:@selector(dismissKeyboard)]];
    self.editor.inputAccessoryView = keyboardToolbar;
    [self.view addSubview:self.editor];
    self.sendButton = [self button:[self ru:@"Отправить" en:@"Send"] selector:@selector(sendText)];
    self.voiceButton = [self button:[self ru:@"Сказать (3 сек.)" en:@"Speak (3 sec.)"] selector:@selector(recordVoice)];
    self.ruButton = [self button:[self ru:@"Подключиться: RU" en:@"Connect: RU"] selector:@selector(pairRu)];
    self.globalButton = [self button:[self ru:@"Подключиться: Global" en:@"Connect: Global"] selector:@selector(pairGlobal)];
    self.cancelButton = [self button:[self ru:@"Отменить подключение" en:@"Cancel pairing"] selector:@selector(cancelPair)];
    self.reconnectButton = [self button:[self ru:@"Повторить соединение" en:@"Reconnect"] selector:@selector(reconnect)];
    self.speechButton = [self button:@"" selector:@selector(toggleSpeech)];
    self.stopButton = [self button:[self ru:@"Выключить микрофон" en:@"Microphone off"] selector:@selector(stopMicrophone)];
    self.stopSpeechButton = [self button:[self ru:@"Остановить озвучку" en:@"Stop speech"] selector:@selector(stopSpeech)];
    self.listenButton = [self button:@"" selector:@selector(toggleListening)];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithTitle:[self ru:@"Ещё" en:@"More"]
        style:UIBarButtonItemStylePlain target:self action:@selector(showMore)];
    __weak AdaConversationViewController *weakSelf = self;
    self.audio.changed = ^{ [weakSelf refresh]; };
    self.client.changed = ^{ [weakSelf refresh]; };
    self.activation = [[AdaVoiceActivation alloc] init];
    self.activation.changed = ^{ [weakSelf refresh]; };
    self.activation.readReply = ^(NSString *text) {
        dispatch_async(dispatch_get_main_queue(), ^{ if (weakSelf.visible && weakSelf.activation.running) [weakSelf readText:text]; });
    };
    self.speech.changed = ^{
        if (weakSelf.speech.busy && ![weakSelf.activation.state isEqual:@"speaking"]) [weakSelf.activation pauseForSpeech];
        if (!weakSelf.speech.busy && ![weakSelf.speech.state isEqual:@"idle"]) [weakSelf.activation speechFinished];
        [weakSelf refresh];
    };
    NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
    [center addObserver:self selector:@selector(leftForeground:) name:UIApplicationWillResignActiveNotification object:nil];
    [center addObserver:self selector:@selector(becameActive:) name:UIApplicationDidBecomeActiveNotification object:nil];
    [center addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillChangeFrameNotification object:nil];
    [center addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillHideNotification object:nil];
    [self refresh];
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (!self.probeTimer) self.probeTimer = [NSTimer scheduledTimerWithTimeInterval:0.5 target:self selector:@selector(checkVoiceUIProbe:) userInfo:nil repeats:YES];
    if (!self.visible) {
        self.visible = YES; self.clientProbe = [[AdaClientUIProbe alloc] init];
        __weak AdaConversationViewController *weakSelf = self;
        self.clientProbe.readReply = ^{
            if (!weakSelf.visible || [UIApplication sharedApplication].applicationState != UIApplicationStateActive) return;
            [weakSelf showMore];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [weakSelf readReplyAfterDismissingMenu]; });
        };
        [self.client start];
    }
    [self runPendingLocalUIProbe];
}
- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    if (self.isMovingFromParentViewController || self.navigationController.isBeingDismissed) {
        [self.probeTimer invalidate]; self.probeTimer = nil;
        self.visible = NO; [self.clientProbe cancel]; [self stopAudio]; [self.client suspend];
    }
}
- (void)keyboard:(NSNotification *)notification {
    CGRect frame = [notification.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
    frame = [self.view convertRect:frame fromView:nil];
    self.keyboardHeight = [notification.name isEqual:UIKeyboardWillHideNotification] ? 0 : MAX(0, CGRectGetMaxY(self.view.bounds) - CGRectGetMinY(frame));
    [self refresh];
}
- (NSString *)stateText {
    NSDictionary *ru = @{@"disconnected": @"Подключите устройство к своей подсети AdaOS.",
        @"creating_pair": @"Готовим подключение…", @"awaiting_approval": @"Отсканируйте QR доверенным телефоном и подтвердите подключение к подсети.",
        @"pair_expired": @"Срок QR истёк. Создайте новое подключение.", @"pair_rejected": @"Подключение не подтверждено. Можно повторить.",
        @"connecting": @"Соединяемся с подсетью…", @"connected": @"Подключено. Можно написать или сказать сообщение.",
        @"offline": @"Связь прервана. Сообщения автоматически не повторяются.", @"authorization_required": @"Подсеть отказала в доступе. Проверьте подключение и права.",
        @"storage_error": @"Не удалось сохранить или прочитать подключение на устройстве.", @"error": @"Сервис подключения недоступен. Проверьте сеть и повторите.", @"suspended": @"Соединение приостановлено."};
    NSDictionary *en = @{@"disconnected": @"Connect this device to your AdaOS subnet.", @"creating_pair": @"Preparing connection…",
        @"awaiting_approval": @"Scan this QR with a trusted phone and approve the subnet connection.", @"pair_expired": @"QR expired. Start a new connection.",
        @"pair_rejected": @"Connection was not approved. You can retry.", @"connecting": @"Connecting to your subnet…",
        @"connected": @"Connected. Type or speak a message.", @"offline": @"Connection interrupted. Messages are not automatically resent.",
        @"authorization_required": @"The subnet refused access. Check your connection and permissions.", @"storage_error": @"Unable to read or save the connection on this device.",
        @"error": @"Connection service unavailable. Check the network and try again.", @"suspended": @"Connection paused."};
    return (self.russian ? ru : en)[self.client.status] ?: [self ru:@"Проверяем соединение…" en:@"Checking connection…"];
}
- (void)refresh {
    [self.activation observe:self.client];
    if (!self.isViewLoaded) return;
    [self.clientProbe observe:self.client];
    NSString *text = [self stateText];
    if (self.client.pairURL) text = [text stringByAppendingFormat:[self ru:@"\nОсталось: %.0f сек." en:@"\nExpires in %.0f sec."], MAX(0, self.client.pairExpiry.timeIntervalSinceNow)];
    if (self.notice.length) text = [text stringByAppendingFormat:@"\n%@", self.notice];
    else if (self.client.voicePending) text = [text stringByAppendingString:[self ru:@"\nРаспознаём голосовое сообщение…" en:@"\nRecognizing voice message…"]];
    else if ([self.client.detail isEqual:@"delivery_unknown_no_retry"]) text = [text stringByAppendingString:[self ru:@"\nДоставка последнего сообщения неизвестна. Проверьте ответы перед повтором." en:@"\nLast delivery is unknown. Check replies before sending again."]];
    else if ([self.client.detail isEqual:@"voice_not_recognized_or_refused"]) text = [text stringByAppendingString:[self ru:@"\nРечь не распознана или запрос отклонён. Можно написать текстом." en:@"\nSpeech was not recognized or was refused. You can type instead."]];
    if (self.client.ready && !self.client.voiceReady) text = [text stringByAppendingString:self.client.voiceChecked ?
        [self ru:@"\nГолос на узле недоступен. Текстовый чат работает." en:@"\nNode voice is unavailable. Text chat remains available."] :
        [self ru:@"\nПроверяем доступность голосовой связи…" en:@"\nChecking voice availability…"]];
    if (self.activation.running && [self.activation.snapshot[@"dropped_segments"] unsignedIntegerValue])
        text = [text stringByAppendingString:[self ru:@"\nЧасть речи не обработана: буфер заполнен, истёк или начался ответ. Дождитесь состояния «Слушаю» перед новой командой." en:@"\nSome speech was not processed: the buffer filled, expired or a reply began. Wait for Listening before a new command."]];
    if (self.speech.busy) text = [text stringByAppendingString:[self ru:@"\nЧитаем ответ…" en:@"\nReading the reply…"]];
    else if ([@[@"unavailable", @"audio-session-error", @"start-timeout", @"playback-timeout"] containsObject:self.speech.state])
        text = [text stringByAppendingString:[self ru:@"\nОзвучка недоступна. Ответ остаётся в чате." en:@"\nSpeech unavailable. The reply remains in chat."]];
    self.stateLabel.text = text;
    BOOL microphone = self.audio.microphoneActive || self.activation.microphoneActive;
    NSString *phase = [self ru:@"Готов к общению" en:@"Ready"];
    if (self.speech.busy || self.audio.playbackActive) phase = [self ru:@"Говорю" en:@"Speaking"];
    else if (!self.client.ready && !self.client.pairURL) phase = [self ru:@"Нет связи" en:@"No connection"];
    else if (self.recording || ([self.activation.state isEqual:@"listening"] && self.activation.microphoneActive)) phase = [self ru:@"Слушаю" en:@"Listening"];
    else if ([self.activation.state isEqual:@"awaiting-command"] && self.activation.running) phase = [self ru:@"Слушаю команду · 6 секунд" en:@"Listening for a command · 6 seconds"];
    else if ([self.activation.state isEqual:@"ready-cue"] && self.activation.running) phase = [self ru:@"Сигнал готовности" en:@"Ready signal"];
    else if (self.client.voicePending || (self.activation.running && [self.activation.state isEqual:@"recognizing"])) phase = [self ru:@"Распознаю" en:@"Recognizing"];
    else if (self.client.waitingForReply || (self.activation.running && [@[@"claiming", @"waiting-reply"] containsObject:self.activation.state])) phase = [self ru:@"Жду ответ" en:@"Waiting for reply"];
    self.voiceStateLabel.text = [NSString stringWithFormat:@"%@\n%@", phase, microphone ? [self ru:@"● Микрофон включён" en:@"● Microphone ON"] : [self ru:@"○ Микрофон выключен" en:@"○ Microphone OFF"]];
    self.voiceStateLabel.textColor = microphone ? [UIColor colorWithRed:0.65 green:0.12 blue:0.08 alpha:1] : [UIColor darkGrayColor];
    [self.voiceSetup observe:self.activation.snapshot speech:[AdaSpeechOutput report]];
    NSString *pairURL = self.client.pairURL.absoluteString;
    if (![self.shownPairURL isEqual:pairURL]) {
        self.shownPairURL = pairURL;
        self.qr.image = nil;
        if (pairURL.length) {
            CIFilter *filter = [CIFilter filterWithName:@"CIQRCodeGenerator"];
            [filter setValue:[pairURL dataUsingEncoding:NSUTF8StringEncoding] forKey:@"inputMessage"];
            [filter setValue:@"M" forKey:@"inputCorrectionLevel"];
            CIImage *image = filter.outputImage;
            self.qr.image = image ? [UIImage imageWithCIImage:[image imageByApplyingTransform:CGAffineTransformMakeScale(8, 8)]] : nil;
        }
    }
    BOOL pair = self.client.pairURL != nil;
    BOOL offer = [@[@"disconnected", @"pair_expired", @"pair_rejected", @"error"] containsObject:self.client.status];
    self.ruButton.hidden = self.globalButton.hidden = !offer;
    self.qr.hidden = !pair;
    self.cancelButton.hidden = !pair && ![self.client.status isEqual:@"creating_pair"];
    self.reconnectButton.hidden = ![@[@"offline", @"authorization_required", @"storage_error"] containsObject:self.client.status];
    self.history.hidden = self.editor.hidden = self.sendButton.hidden = self.voiceButton.hidden = pair || offer || [self.client.status isEqual:@"creating_pair"];
    self.editor.editable = self.client.ready && !self.recording;
    self.sendButton.enabled = self.client.ready && !self.recording && self.editor.text.length > 0 && self.editor.text.length <= 4000;
    self.voiceButton.enabled = self.client.ready && self.client.voiceReady && !self.recording && !self.client.voicePending;
    [self.speechButton setTitle:self.readReplies ? [self ru:@"Системный голос: читать ответы" en:@"System voice: read replies"] :
        [self ru:@"Системный голос: выключен" en:@"System voice: off"] forState:UIControlStateNormal];
    self.speechButton.hidden = self.history.hidden || self.keyboardHeight > 0;
    self.voiceButton.hidden = self.history.hidden || self.keyboardHeight > 0;
    self.stopButton.hidden = !self.recording && !self.speech.busy && !self.client.voicePending && !self.activation.running;
    self.stopSpeechButton.hidden = self.stopButton.hidden;
    self.stopSpeechButton.enabled = self.speech.busy || self.audio.playbackActive;
    self.stopButton.enabled = self.audio.microphoneActive || self.client.voicePending || self.activation.running;
    self.listenButton.hidden = self.history.hidden || self.keyboardHeight > 0;
    self.listenButton.enabled = self.activation.running || (self.client.ready && self.client.voiceReady && !self.recording && !self.client.voicePending);
    [self.listenButton setTitle:self.activation.running ? [self ru:@"Прослушивание включено" en:@"Listening enabled"] :
        (self.voiceSetup.enabled ? [self ru:@"Включить прослушивание" en:@"Start listening"] : [self ru:@"Настроить голос" en:@"Set up voice"]) forState:UIControlStateNormal];
    self.setupView.hidden = !self.showingSetup || (!self.client.ready && (pair || offer || [self.client.status isEqual:@"creating_pair"]));
    if (!self.setupView.hidden) {
        self.history.hidden = self.editor.hidden = self.sendButton.hidden = self.voiceButton.hidden = self.speechButton.hidden = self.listenButton.hidden = YES;
        [self renderVoiceSetup];
    }
    NSMutableArray *lines = [NSMutableArray array];
    for (NSDictionary *entry in self.client.messages) {
        BOOL user = [entry[@"from"] isEqual:@"user"];
        NSString *label = user ? [self ru:@"Вы" en:@"You"] : (entry[@"sender"] ?: @"AdaOS");
        NSString *delivery = entry[@"delivery"];
        NSString *suffix = @"";
        if ([delivery isEqual:@"sending"]) suffix = [self ru:@" · отправляется" en:@" · sending"];
        else if ([delivery isEqual:@"unknown"]) suffix = [self ru:@" · доставка неизвестна" en:@" · delivery unknown"];
        else if ([delivery isEqual:@"refused"] || [delivery isEqual:@"not_sent"]) suffix = [self ru:@" · не отправлено" en:@" · not sent"];
        [lines addObject:[NSString stringWithFormat:@"%@%@\n%@", label, suffix, entry[@"text"]]];
        if (!user && ![self.spoken containsObject:entry[@"id"]]) {
            [self.spoken addObject:entry[@"id"]];
            if (self.readReplies && !self.activation.running && [entry[@"automatic_speech"] boolValue] && self.visible && !self.recording && !self.speech.busy) [self readText:entry[@"text"]];
        }
    }
    if (self.spoken.count > 120) {
        [self.spoken removeAllObjects];
        for (NSDictionary *entry in self.client.messages) [self.spoken addObject:entry[@"id"]];
    }
    NSString *history = [lines componentsJoinedByString:@"\n\n"];
    if (![self.history.text isEqual:history]) {
        BOOL atBottom = self.history.contentOffset.y + self.history.bounds.size.height >= self.history.contentSize.height - 60;
        self.history.text = history;
        if (atBottom && history.length) [self.history scrollRangeToVisible:NSMakeRange(history.length - 1, 1)];
    }
    [self.view setNeedsLayout];
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGFloat width = self.view.bounds.size.width, left = MAX(16, (width - 720) / 2), w = width - 2 * left;
    CGFloat top = self.topLayoutGuide.length + 12, bottom = self.view.bounds.size.height - MAX(self.keyboardHeight, self.bottomLayoutGuide.length) - 12;
    self.voiceStateLabel.frame = CGRectMake(left, top, w, 56); top += 64;
    CGFloat stateHeight = MIN(self.keyboardHeight > 0 ? 38 : 88, MAX(30, [self.stateLabel sizeThatFits:CGSizeMake(w, 200)].height));
    self.stateLabel.frame = CGRectMake(left, top, w, stateHeight); top += stateHeight + 10;
    self.client.transport.view.frame = CGRectMake(0, 0, 1, 1);
    if (!self.qr.hidden) {
        CGFloat size = MAX(100, MIN(300, MIN(w, bottom - top - 66)));
        self.qr.frame = CGRectMake((width - size) / 2, top, size, size);
        self.cancelButton.frame = CGRectMake(left, top + size + 12, w, 52);
    } else if (!self.ruButton.hidden) {
        self.ruButton.frame = CGRectMake(left, top + 12, w, 56);
        self.globalButton.frame = CGRectMake(left, top + 80, w, 56);
    } else if (!self.cancelButton.hidden) self.cancelButton.frame = CGRectMake(left, top + 12, w, 56);
    if (!self.reconnectButton.hidden) { self.reconnectButton.frame = CGRectMake(left, top, w, 52); top += 62; }
    CGFloat stopHeight = self.stopButton.hidden ? 0 : 52;
    if (!self.setupView.hidden) {
        self.setupView.frame = CGRectMake(left, top, w, MAX(0, bottom-top-stopHeight));
        CGFloat y = 0;
        for (UIView *row in self.setupRows) {
            CGFloat h = [row isKindOfClass:[UILabel class]] ? [row sizeThatFits:CGSizeMake(w-8, CGFLOAT_MAX)].height+12 : MAX(52, [((UIButton *)row).titleLabel sizeThatFits:CGSizeMake(w-24, CGFLOAT_MAX)].height+20);
            row.frame = CGRectMake(0,y,w,h); y += h+10;
        }
        self.setupView.contentSize = CGSizeMake(w,y);
        self.stopButton.frame = CGRectMake(left,bottom-44,w/2-4,44);
        self.stopSpeechButton.frame = CGRectMake(left+w/2+4,bottom-44,w/2-4,44);
    } else if (!self.history.hidden) {
        CGFloat controls = (self.keyboardHeight > 0 ? 0 : 112) + stopHeight;
        CGFloat composerHeight = self.keyboardHeight > 0 ? 52 : 72;
        CGFloat historyHeight = MAX(0, bottom - top - controls - composerHeight - 8);
        self.history.frame = CGRectMake(left, top, w, historyHeight);
        CGFloat y = top + historyHeight + 6;
        self.editor.frame = CGRectMake(left, y, MAX(80, w - 100), composerHeight);
        self.sendButton.frame = CGRectMake(left + w - 90, y, 90, composerHeight); y += composerHeight + 8;
        self.voiceButton.frame = CGRectMake(left, y, w/2-4, 48);
        self.listenButton.frame = CGRectMake(left+w/2+4,y,w/2-4,48); if (self.keyboardHeight <= 0) y += 56;
        self.speechButton.frame = CGRectMake(left, y, w, 48); y += 56;
        if (self.keyboardHeight > 0) y -= 56;
        self.stopButton.frame = CGRectMake(left, y, w/2-4, 44);
        self.stopSpeechButton.frame = CGRectMake(left+w/2+4,y,w/2-4,44);
    }
    [AdaVoiceUIProbe presentation:@{@"label": self.voiceStateLabel.text ?: @"", @"microphone_active": @(self.audio.microphoneActive || self.activation.microphoneActive),
        @"connection_state": self.client.status ?: @"disconnected", @"voice_ready": @(self.client.voiceReady), @"voice_checked": @(self.client.voiceChecked),
        @"header_visible": @(!self.voiceStateLabel.hidden && CGRectContainsRect(self.view.bounds,self.voiceStateLabel.frame)),
        @"setup_visible": @(!self.setupView.hidden), @"stop_microphone_visible": @(!self.stopButton.hidden),
        @"stop_speech_visible": @(!self.stopSpeechButton.hidden),
        @"stop_buttons_in_bounds": @(self.stopButton.hidden || (CGRectContainsRect(self.view.bounds,self.stopButton.frame) && CGRectContainsRect(self.view.bounds,self.stopSpeechButton.frame)))}];
}
- (void)pairRu { self.notice = nil; [self.client pairInZone:@"ru"]; }
- (void)pairGlobal { self.notice = nil; [self.client pairInZone:@"global"]; }
- (void)cancelPair { self.notice = nil; [self.client cancelPairing]; }
- (void)reconnect { self.notice = nil; [self.client start]; }
- (void)textViewDidChange:(UITextView *)textView { (void)textView; [self refresh]; }
- (void)dismissKeyboard { [self.editor resignFirstResponder]; }
- (void)sendText {
    if (self.activation.running) [self.activation stop:@"stopped"];
    if ([self.client sendText:self.editor.text]) { self.editor.text = @""; self.notice = nil; [self refresh]; }
}
- (void)recordVoice {
    if (!self.client.ready || !self.client.voiceReady || self.recording || self.client.voicePending) return;
    [self.activation stop:@"stopped"]; [self.speech cancel];
    if (![[LabAudioProbe permission] isEqual:@"granted"]) {
        [LabAudioProbe requestPermissionWithCompletion:^(NSString *status) {
            (void)status;
            self.notice = [[LabAudioProbe permission] isEqual:@"granted"] ?
                [self ru:@"Микрофон разрешён. Нажмите «Сказать», когда будете готовы." en:@"Microphone allowed. Press Speak when ready."] :
                [self ru:@"Микрофон запрещён. Можно написать сообщение." en:@"Microphone denied. You can type a message."];
            [self refresh];
        }]; return;
    }
    self.recording = YES; NSUInteger generation = ++self.generation;
    self.notice = [self ru:@"Говорите сейчас: три секунды. Запись будет отправлена в вашу подсеть." en:@"Speak now: three seconds. Audio will be sent to your subnet."];
    [self.editor resignFirstResponder]; [self refresh];
    [self.audio captureWAVWithCompletion:^(NSData *wav, NSString *status) {
        (void)status;
        if (generation != self.generation || !self.visible) return;
        self.recording = NO; self.notice = nil;
        if (!wav || ![self.client sendVoice:wav russian:self.russian])
            self.notice = [self ru:@"Запись не отправлена. Повторите или напишите текстом." en:@"Recording was not sent. Retry or type instead."];
        [self refresh];
    }];
    [self refresh]; /* Capture starts synchronously; show its actual hardware state. */
}
- (void)readText:(NSString *)text {
    if (!self.visible || self.recording || !text.length) return;
    [self.activation pauseForSpeech];
    NSString *language = self.russian ? @"ru-RU" : @"en-US";
    if ([text rangeOfCharacterFromSet:[NSCharacterSet characterSetWithRange:NSMakeRange(0x0400, 0x100)]].location != NSNotFound) language = @"ru-RU";
    self.notice = nil;
    if (text.length > 2000) self.notice = [self ru:@"Озвучивается начало длинного ответа. Полный текст доступен в чате." en:@"Reading the beginning of a long reply. The full text is available in chat."];
    [self.speech speak:text.length > 2000 ? [text substringToIndex:2000] : text language:language];
}
- (void)readLastReply {
    for (NSDictionary *entry in self.client.messages.reverseObjectEnumerator)
        if ([entry[@"from"] isEqual:@"assistant"]) { [self readText:entry[@"text"]]; return; }
    self.notice = [self ru:@"Ответа для чтения пока нет." en:@"No reply to read yet."]; [self refresh];
}
- (void)readReplyAfterDismissingMenu {
    UIViewController *presented = self.presentedViewController;
    if (presented) [presented dismissViewControllerAnimated:YES completion:^{ [self readLastReply]; }];
    else [self readLastReply];
}
- (void)toggleSpeech {
    self.readReplies = !self.readReplies;
    [[NSUserDefaults standardUserDefaults] setBool:self.readReplies forKey:@"adaos.ios.read-replies"];
    [[NSUserDefaults standardUserDefaults] synchronize];
    if (!self.readReplies) [self.speech cancel];
    [self refresh];
}
- (void)stopAudio {
    [self.activation stop:@"stopped"];
    BOOL wasRecording = self.recording;
    ++self.generation; [self.audio cancel]; [self.speech cancel];
    self.recording = NO;
    if (wasRecording) self.notice = [self ru:@"Запись остановлена и не отправлена." en:@"Recording stopped and not sent."];
    if (self.client.voicePending) {
        [self.client cancelVoiceInput];
        self.notice = [self ru:@"Ожидание остановлено. Отправленная запись могла быть обработана. Проверьте ответы после соединения."
            en:@"Waiting stopped. Submitted audio may have been processed. Check replies after reconnecting."];
    }
    [self refresh];
}
- (void)setupText:(NSString *)text {
    UILabel *label = [[UILabel alloc] init]; label.numberOfLines = 0; label.text = text;
    label.font = [UIFont systemFontOfSize:20]; [self.setupRows addObject:label]; [self.setupView addSubview:label];
}
- (UIButton *)setupButton:(NSString *)title selector:(SEL)selector {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem]; button.titleLabel.font = [UIFont systemFontOfSize:20];
    button.titleLabel.numberOfLines = 0; button.titleLabel.textAlignment = NSTextAlignmentCenter;
    button.backgroundColor = [UIColor colorWithWhite:0.93 alpha:1]; button.layer.cornerRadius = 6;
    [button setTitle:title forState:UIControlStateNormal]; [button addTarget:self action:selector forControlEvents:UIControlEventTouchUpInside];
    [self.setupRows addObject:button]; [self.setupView addSubview:button]; return button;
}
- (void)renderVoiceSetup {
    NSDictionary *report = self.voiceSetup.report;
    NSString *signature = [NSString stringWithFormat:@"%@/%@/%d/%d/%d/%d", report, [LabAudioProbe permission], self.client.voiceReady, self.client.voiceChecked, self.activation.running, self.recording];
    if ([self.setupSignature isEqual:signature]) return; self.setupSignature = signature;
    for (UIView *row in self.setupRows) [row removeFromSuperview]; self.setupRows = [NSMutableArray array];
    [self setupText:[self ru:@"Голосовое управление «Арсений»" en:@"Arseniy voice control"]];
    [self setupText:[self ru:@"Включается только по вашей кнопке и работает, пока AdaOS открыт на экране. Речевые фрагменты отправляются в вашу подсеть. Во время ответа микрофон выключен. После блокировки или ухода из приложения включите режим снова."
        en:@"Starts only when you press the button and works while AdaOS is on screen. Speech segments go to your subnet. The microphone is off during replies. After locking or leaving the app, start listening again."]];
    [self setupButton:self.voiceSetup.enabled ? [self ru:@"Длительное прослушивание: разрешено" en:@"Long listening: enabled"] : [self ru:@"Длительное прослушивание: выключено" en:@"Long listening: off"] selector:@selector(toggleLongPreference)];
    NSString *mic = [[LabAudioProbe permission] isEqual:@"granted"] ? [self ru:@"разрешён" en:@"allowed"] : [self ru:@"нужно разрешение" en:@"permission needed"];
    NSString *node = self.client.voiceReady && [AdaVoiceActivation targetFromDescriptor:self.client.activationTargets] ? [self ru:@"голос и имя доступны" en:@"voice and name available"] :
        (self.client.voiceChecked ? [self ru:@"голос или имя недоступны" en:@"voice or name unavailable"] : [self ru:@"проверяем" en:@"checking"]);
    [self setupText:[NSString stringWithFormat:[self ru:@"Микрофон: %@. Узел: %@." en:@"Microphone: %@. Node: %@."],mic,node]];
    if (![[LabAudioProbe permission] isEqual:@"granted"]) [self setupButton:[self ru:@"Разрешить микрофон" en:@"Allow microphone"] selector:@selector(allowSetupMicrophone)];
    UIButton *local = [self setupButton:[self ru:@"Проверить микрофон без узла: запись и повтор" en:@"Check microphone without node: record and replay"] selector:@selector(testLocalSetupMicrophone)];
    local.enabled = !self.recording && !self.activation.running && !self.audio.running && [[LabAudioProbe permission] isEqual:@"granted"];
    [self setupText:[self.voiceSetup.report[@"local_replay_completed"] boolValue] ? [self ru:@"Локальная запись и повтор завершены. Это не проверяет связь с узлом." en:@"Local capture and replay completed. This does not check node connectivity."] : [self ru:@"Локальная проба: 3 секунды записи, затем повтор. Файл удаляется; отправки в сеть нет." en:@"Local test: 3 seconds of recording, then replay. The file is deleted; no network upload."]];
    [self setupText:[self ru:@"Проба на 90 секунд: скажите только «Арсений», дождитесь короткого сигнала, затем в течение 6 секунд начните «Проверка связи». Имя и команду также можно произнести одной фразой."
        en:@"90-second test: say just Arseniy, wait for the short tone, then begin your command within 6 seconds. You can also say the name and command together."]];
    UIButton *test = [self setupButton:[self ru:@"Начать / повторить пробу обращения" en:@"Start / repeat address test"] selector:@selector(testVoiceSetup)];
    test.enabled = !self.recording && !self.audio.running && !self.activation.running && self.client.voiceReady && [[LabAudioProbe permission] isEqual:@"granted"] && [AdaVoiceActivation targetFromDescriptor:self.client.activationTargets] != nil;
    NSMutableArray *lines = [NSMutableArray array];
    NSArray *keys = @[@"microphone", @"node", @"address", @"reply", @"synthesis"];
    NSArray *names = self.russian ? @[@"Звук микрофона", @"Распознавание на узле", @"Отдельное обращение и сигнал", @"Ответ на следующую команду", @"Завершение озвучки"] :
        @[@"Microphone audio", @"Node recognition", @"Separate address and tone", @"Reply to follow-up", @"Speech completion"];
    for (NSUInteger i=0; i<keys.count; i++) [lines addObject:[NSString stringWithFormat:@"%@: %@", names[i], [report[keys[i]] boolValue] ? [self ru:@"проверено" en:@"checked"] : [self ru:@"не проверено" en:@"not checked"]]];
    [lines addObject:[NSString stringWithFormat:[self ru:@"Слышимость ответа: %@" en:@"Reply audibility: %@"], [report[@"hearing_checked"] boolValue] ? ([report[@"heard"] boolValue] ? [self ru:@"подтверждена вами" en:@"confirmed by you"] : [self ru:@"не слышно / неразборчиво" en:@"not heard / unclear"]) : [self ru:@"нужно ваше подтверждение" en:@"needs your confirmation"]]];
    [self setupText:[lines componentsJoinedByString:@"\n"]];
    UIButton *heard = [self setupButton:[self ru:@"Ответ слышен разборчиво" en:@"Reply heard clearly"] selector:@selector(confirmSetupHeard)];
    UIButton *unclear = [self setupButton:[self ru:@"Не слышно / неразборчиво" en:@"Not heard / unclear"] selector:@selector(confirmSetupUnclear)];
    heard.enabled = unclear.enabled = [report[@"synthesis"] boolValue];
    [self setupText:[report[@"ready"] boolValue] ? [self ru:@"Голосовой режим проверен. В чате включайте его кнопкой «Включить прослушивание»." en:@"Voice checks passed. Start listening with the button in chat."] :
        [self ru:@"Проверка пока неполная. Текстовый чат доступен; можно закончить настройку и вернуться позже." en:@"Checks are incomplete. Text chat is available; finish and return later."]];
    [self setupText:[self ru:@"«Остановить озвучку» прерывает звук. «Выключить микрофон» прекращает запись и локальное ожидание. Эти кнопки не отменяют уже отправленную задачу на узле. Голосовое прерывание во время ответа пока не поддерживается."
        en:@"Stop speech interrupts sound. Microphone off stops capture and local waiting. These buttons do not cancel an already submitted node task. Voice interruption during a reply is not supported yet."]];
    [self setupButton:[self ru:@"Сохранить и перейти в чат" en:@"Save and open chat"] selector:@selector(finishVoiceSetup)];
}
- (void)openVoiceSetup { [self stopAudio]; [self.editor resignFirstResponder]; self.showingSetup = YES; self.setupSignature = nil; [self refresh]; }
- (void)toggleLongPreference {
    self.voiceSetup.enabled = !self.voiceSetup.enabled;
    if (!self.voiceSetup.enabled) [self stopMicrophone];
    [self refresh];
}
- (void)allowSetupMicrophone {
    [LabAudioProbe requestPermissionWithCompletion:^(NSString *status) { (void)status; [self refresh]; }];
}
- (void)testLocalSetupMicrophone {
    if (self.audio.running || ![[LabAudioProbe permission] isEqual:@"granted"]) return;
    [self stopAudio]; self.recording = YES; NSUInteger generation = ++self.generation;
    [self.voiceSetup localReplayCompleted:NO];
    self.notice = [self ru:@"Говорите 3 секунды. Затем послушайте локальный повтор." en:@"Speak for 3 seconds, then listen to the local replay."];
    [self.audio recordShortSampleAndReplayWithCompletion:^(NSString *status) {
        if (generation != self.generation) return;
        self.recording = NO;
        [self.voiceSetup localReplayCompleted:[status hasPrefix:@"Audio replay OK."] && [status hasSuffix:@"Local test file removed."]];
        self.notice = [self.voiceSetup.report[@"local_replay_completed"] boolValue] ? [self ru:@"Повтор завершён, запись удалена. Можно проверить обращение, когда узел доступен." en:@"Replay completed; recording deleted. Test the address when the node is available."] : [self ru:@"Локальная проба остановлена или не завершилась. Можно повторить." en:@"Local test stopped or did not complete. You can retry."];
        [self refresh];
    }];
    [self refresh];
}
- (void)testVoiceSetup {
    [self stopAudio]; [self.voiceSetup begin]; self.notice = nil;
    if (![self.activation start:self.client]) self.notice = [self ru:@"Проба не началась. Проверьте разрешение и подключение." en:@"Test did not start. Check permission and connection."];
    [self refresh];
}
- (void)confirmSetupHeard { [self.voiceSetup confirmHeard:YES]; [self stopAudio]; [self refresh]; }
- (void)confirmSetupUnclear { [self.voiceSetup confirmHeard:NO]; [self stopAudio]; [self refresh]; }
- (void)finishVoiceSetup { [self stopAudio]; [self.voiceSetup finish]; self.showingSetup = NO; self.notice = nil; [self refresh]; }
- (void)stopMicrophone {
    BOOL submitted = self.client.voicePending || [self.activation.snapshot[@"dispatched"] unsignedIntegerValue] > 0;
    [self.activation stop:@"stopped"];
    if (!self.audio.playbackActive) { ++self.generation; [self.audio cancel]; self.recording = NO; }
    [self.client cancelVoiceInput];
    self.notice = submitted ? [self ru:@"Микрофон выключен. Уже отправленная задача на узле не отменена." en:@"Microphone off. An already submitted node task is not cancelled."] :
        [self ru:@"Микрофон выключен. Неотправленная запись удалена." en:@"Microphone off. Unsent audio discarded."];
    [self refresh];
}
- (void)stopSpeech {
    [self.speech cancel];
    if (self.audio.playbackActive) [self.audio cancel];
    self.notice = [self ru:@"Озвучка остановлена. Это не отменяет задачу на узле." en:@"Speech stopped. This does not cancel the node task."];
    [self refresh];
}
- (void)toggleListening {
    if (self.activation.running) { [self stopMicrophone]; return; }
    if (!self.voiceSetup.enabled) { [self openVoiceSetup]; return; }
    [self.speech cancel]; self.notice = nil;
    if (![self.activation start:self.client continuous:YES]) self.notice = [self ru:@"Не удалось включить микрофон. Проверьте разрешение, связь и имя Арсений на узле." en:@"Cannot start listening. Check microphone permission, connection and Arseniy on the node."];
    [self refresh];
}
- (void)checkVoiceUIProbe:(NSTimer *)timer {
    (void)timer;
    if (!self.visible || [UIApplication sharedApplication].applicationState != UIApplicationStateActive) return;
    NSString *action = [AdaVoiceUIProbe consumeForMicrophone:!self.stopButton.hidden && self.stopButton.enabled speech:!self.stopSpeechButton.hidden && self.stopSpeechButton.enabled]; if (!action) return;
    if ([action isEqual:@"stop-microphone"]) [self.stopButton sendActionsForControlEvents:UIControlEventTouchUpInside];
    else if ([action isEqual:@"stop-speech"]) [self.stopSpeechButton sendActionsForControlEvents:UIControlEventTouchUpInside];
    else if ([action isEqual:@"disconnect"]) [self.client suspend];
    else if ([action isEqual:@"open-setup"]) [self openVoiceSetup];
    else if ([action isEqual:@"start-setup"]) [self testVoiceSetup];
    else if ([action isEqual:@"finish-setup"]) [self finishVoiceSetup];
    else if ([action isEqual:@"local-microphone"]) [self testLocalSetupMicrophone];
    else if ([action isEqual:@"reconnect"]) [self.reconnectButton sendActionsForControlEvents:UIControlEventTouchUpInside];
    [AdaVoiceUIProbe save:action microphone:self.audio.microphoneActive || self.activation.microphoneActive speaking:self.speech.busy];
}
- (void)showMore {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Conversational" message:nil preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Настроить голос и прослушивание" en:@"Set up voice and listening"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        (void)action;
        [self openVoiceSetup];
    }]];
    if (self.client.pairURL) {
        [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Скопировать ссылку подключения" en:@"Copy pairing link"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
            (void)action; [UIPasteboard generalPasteboard].string = self.client.pairURL.absoluteString;
            self.notice = [self ru:@"Ссылка скопирована. Откройте её в современном браузере с доступом к вашей подсети."
                en:@"Link copied. Open it in a modern browser with access to your subnet."]; [self refresh];
        }]];
        [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Открыть ссылку в браузере" en:@"Open pairing link in browser"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
            (void)action; if (self.client.pairURL) [[UIApplication sharedApplication] openURL:self.client.pairURL];
        }]];
    }
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Прочитать последний ответ" en:@"Read the last reply"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        (void)action;
        [self readReplyAfterDismissingMenu];
    }]];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Удалить подключение на устройстве" en:@"Remove connection from this device"] style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action) {
        (void)action;
        UIAlertController *confirm = [UIAlertController alertControllerWithTitle:[self ru:@"Удалить подключение?" en:@"Remove connection?"]
            message:[self ru:@"Для следующего входа потребуется новое подтверждение. Это удаляет подключение только с этого устройства."
                en:@"A new approval will be needed. This removes the connection from this device only."] preferredStyle:UIAlertControllerStyleAlert];
        [confirm addAction:[UIAlertAction actionWithTitle:[self ru:@"Отмена" en:@"Cancel"] style:UIAlertActionStyleCancel handler:nil]];
        [confirm addAction:[UIAlertAction actionWithTitle:[self ru:@"Удалить" en:@"Remove"] style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action2) {
            (void)action2; [self stopAudio]; self.notice = nil; [self.client forget]; self.editor.text = @""; [self refresh];
        }]];
        [self presentViewController:confirm animated:YES completion:nil];
    }]];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Закрыть" en:@"Close"] style:UIAlertActionStyleCancel handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}
- (void)leftForeground:(NSNotification *)notification {
    (void)notification; if (self.visible) { [self.clientProbe cancel]; [self stopAudio]; [self.activation stop:@"background"]; [self.client suspend]; }
}
- (void)becameActive:(NSNotification *)notification { (void)notification; if (self.visible) [self.client start]; }
- (void)runPendingLocalUIProbe {
    if (![AdaLocalUIProbe consume]) return;
    __weak AdaConversationViewController *weakSelf = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        AdaConversationViewController *owner = weakSelf;
        if (!owner.visible || [UIApplication sharedApplication].applicationState != UIApplicationStateActive) return;
        /* Focus is permitted before subnet registration; the probe sends no text. */
        owner.editor.editable = YES; owner.editor.hidden = NO; [owner.editor becomeFirstResponder];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            BOOL shown = owner.keyboardHeight > 0 && owner.editor.isFirstResponder;
            [owner refresh]; /* Reproduce a refresh while the real keyboard is visible. */
            [owner.editor resignFirstResponder];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
                if (!owner.visible) return;
                [owner.view layoutIfNeeded];
                BOOL dismissed = owner.keyboardHeight == 0 && !owner.editor.isFirstResponder;
                BOOL restored = !owner.voiceButton.hidden && !owner.speechButton.hidden &&
                    CGRectContainsRect(owner.view.bounds, owner.voiceButton.frame) && CGRectContainsRect(owner.view.bounds, owner.speechButton.frame);
                [AdaLocalUIProbe saveKeyboardShown:shown dismissed:dismissed restored:restored];
                [owner readText:@"This is an AdaOS voice check."];
            });
        });
    });
}
- (void)dealloc { [self.probeTimer invalidate]; self.speech.changed = nil; [self.client suspend]; [self.audio cancel]; [self.speech cancel]; [[NSNotificationCenter defaultCenter] removeObserver:self]; }
@end
