#import "AdaConversationViewController.h"
#import "AdaSubnetClient.h"
#import "AdaOnboardingStore.h"
#import "LabAudioProbe.h"
#import "AdaClientUIProbe.h"
#import "AdaSpeechOutput.h"
#import "AdaLocalUIProbe.h"
#import <AVFoundation/AVFoundation.h>
#import <CoreImage/CoreImage.h>

@interface AdaConversationViewController () <UITextViewDelegate>
@property(nonatomic, strong) AdaSubnetClient *client;
@property(nonatomic, strong) AdaClientUIProbe *clientProbe;
@property(nonatomic, strong) LabAudioProbe *audio;
@property(nonatomic, strong) AdaSpeechOutput *speech;
@property(nonatomic, strong) UILabel *stateLabel;
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
    [self.view addSubview:self.client.transport.view];
    self.stateLabel = [[UILabel alloc] init]; self.stateLabel.numberOfLines = 0;
    self.stateLabel.font = [UIFont systemFontOfSize:18]; [self.view addSubview:self.stateLabel];
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
    self.stopButton = [self button:[self ru:@"Остановить звук / запись" en:@"Stop speech / recording"] selector:@selector(stopAudio)];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithTitle:[self ru:@"Ещё" en:@"More"]
        style:UIBarButtonItemStylePlain target:self action:@selector(showMore)];
    __weak AdaConversationViewController *weakSelf = self;
    self.client.changed = ^{ [weakSelf refresh]; };
    self.speech.changed = ^{ [weakSelf refresh]; };
    NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
    [center addObserver:self selector:@selector(leftForeground:) name:UIApplicationWillResignActiveNotification object:nil];
    [center addObserver:self selector:@selector(becameActive:) name:UIApplicationDidBecomeActiveNotification object:nil];
    [center addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillChangeFrameNotification object:nil];
    [center addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillHideNotification object:nil];
    [self refresh];
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (!self.visible) { self.visible = YES; self.clientProbe = [[AdaClientUIProbe alloc] init]; [self.client start]; }
    [self runPendingLocalUIProbe];
}
- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    if (self.isMovingFromParentViewController || self.navigationController.isBeingDismissed) {
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
    if (self.speech.busy) text = [text stringByAppendingString:[self ru:@"\nЧитаем ответ…" en:@"\nReading the reply…"]];
    else if ([@[@"unavailable", @"audio-session-error", @"start-timeout", @"playback-timeout"] containsObject:self.speech.state])
        text = [text stringByAppendingString:[self ru:@"\nОзвучка недоступна. Ответ остаётся в чате." en:@"\nSpeech unavailable. The reply remains in chat."]];
    self.stateLabel.text = text;
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
    self.stopButton.hidden = !self.recording && !self.speech.busy && !self.client.voicePending;
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
            if (self.readReplies && [entry[@"automatic_speech"] boolValue] && self.visible && !self.recording && !self.speech.busy) [self readText:entry[@"text"]];
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
    CGFloat stateHeight = MIN(self.keyboardHeight > 0 ? 58 : 132, MAX(50, [self.stateLabel sizeThatFits:CGSizeMake(w, 200)].height));
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
    if (!self.history.hidden) {
        CGFloat controls = self.keyboardHeight > 0 ? 0 : (self.stopButton.hidden ? 112 : 164);
        CGFloat composerHeight = self.keyboardHeight > 0 ? 52 : 72;
        CGFloat historyHeight = MAX(0, bottom - top - controls - composerHeight - 8);
        self.history.frame = CGRectMake(left, top, w, historyHeight);
        CGFloat y = top + historyHeight + 6;
        self.editor.frame = CGRectMake(left, y, MAX(80, w - 100), composerHeight);
        self.sendButton.frame = CGRectMake(left + w - 90, y, 90, composerHeight); y += composerHeight + 8;
        self.voiceButton.frame = CGRectMake(left, y, w, 48); y += 56;
        self.speechButton.frame = CGRectMake(left, y, w, 48); y += 56;
        self.stopButton.frame = CGRectMake(left, y, w, 44);
    }
}
- (void)pairRu { self.notice = nil; [self.client pairInZone:@"ru"]; }
- (void)pairGlobal { self.notice = nil; [self.client pairInZone:@"global"]; }
- (void)cancelPair { self.notice = nil; [self.client cancelPairing]; }
- (void)reconnect { self.notice = nil; [self.client start]; }
- (void)textViewDidChange:(UITextView *)textView { (void)textView; [self refresh]; }
- (void)dismissKeyboard { [self.editor resignFirstResponder]; }
- (void)sendText {
    if ([self.client sendText:self.editor.text]) { self.editor.text = @""; self.notice = nil; [self refresh]; }
}
- (void)recordVoice {
    if (!self.client.ready || !self.client.voiceReady || self.recording || self.client.voicePending) return;
    [self.speech cancel];
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
}
- (void)readText:(NSString *)text {
    if (!self.visible || self.recording || !text.length) return;
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
- (void)toggleSpeech {
    self.readReplies = !self.readReplies;
    [[NSUserDefaults standardUserDefaults] setBool:self.readReplies forKey:@"adaos.ios.read-replies"];
    [[NSUserDefaults standardUserDefaults] synchronize];
    if (!self.readReplies) [self.speech cancel];
    [self refresh];
}
- (void)stopAudio {
    BOOL wasRecording = self.recording;
    ++self.generation; [self.audio cancel]; [self.speech cancel];
    self.recording = NO;
    if (wasRecording) self.notice = [self ru:@"Запись остановлена и не отправлена." en:@"Recording stopped and not sent."];
    if (self.client.voicePending) {
        [self.client suspend];
        self.notice = [self ru:@"Ожидание остановлено. Отправленная запись могла быть обработана. Проверьте ответы после соединения."
            en:@"Waiting stopped. Submitted audio may have been processed. Check replies after reconnecting."];
    }
    [self refresh];
}
- (void)showMore {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Conversational" message:nil preferredStyle:UIAlertControllerStyleAlert];
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
        UIViewController *presented = self.presentedViewController;
        if (presented) [presented dismissViewControllerAnimated:YES completion:^{ [self readLastReply]; }];
        else [self readLastReply];
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
    (void)notification; if (self.visible) { [self.clientProbe cancel]; [self stopAudio]; [self.client suspend]; }
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
- (void)dealloc { self.speech.changed = nil; [self.client suspend]; [self.audio cancel]; [self.speech cancel]; [[NSNotificationCenter defaultCenter] removeObserver:self]; }
@end
