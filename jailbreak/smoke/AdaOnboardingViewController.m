#import "AdaOnboardingViewController.h"
#import "AdaOnboardingStore.h"
#import "LabAudioProbe.h"
#import <AVFoundation/AVFoundation.h>
#import <math.h>

@interface AdaOnboardingViewController () <AVAudioPlayerDelegate>
@property(nonatomic, strong) AdaOnboardingStore *store;
@property(nonatomic, strong) UIScrollView *scroll;
@property(nonatomic, strong) NSMutableArray *rows;
@property(nonatomic, strong) NSDictionary *catalog;
@property(nonatomic, strong) AVAudioPlayer *prompt;
@property(nonatomic, strong) LabAudioProbe *audio;
@property(nonatomic, copy) NSString *notice;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) BOOL busy;
@end

@implementation AdaOnboardingViewController
- (NSString *)ru:(NSString *)ru en:(NSString *)en { return self.store.state.russian ? ru : en; }
- (void)viewDidLoad {
    [super viewDidLoad];
    self.store = [[AdaOnboardingStore alloc] init];
    self.audio = [[LabAudioProbe alloc] init];
    self.view.backgroundColor = [UIColor whiteColor];
    self.scroll = [[UIScrollView alloc] initWithFrame:self.view.bounds];
    self.scroll.alwaysBounceVertical = YES;
    [self.view addSubview:self.scroll];
    NSURL *url = [[NSBundle mainBundle] URLForResource:@"onboarding-prompts" withExtension:@"json"];
    NSData *data = url ? [NSData dataWithContentsOfURL:url] : nil;
    if (data.length <= 65536) {
        id parsed = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if ([parsed isKindOfClass:[NSDictionary class]]) self.catalog = parsed[@"locales"];
    }
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithTitle:@"…"
        style:UIBarButtonItemStylePlain target:self action:@selector(showMore)];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(interrupted:)
        name:UIApplicationWillResignActiveNotification object:nil];
    [self render];
}
- (void)viewWillAppear:(BOOL)animated { [super viewWillAppear:animated]; if (self.store) [self render]; }
- (NSDictionary *)currentPrompt {
    NSString *locale = self.store.state.russian ? @"ru" : @"en";
    NSString *step = [NSString stringWithUTF8String:AdaOnboardingStepID(self.store.state.step)];
    id entry = self.catalog[locale][step];
    return [entry isKindOfClass:[NSDictionary class]] ? entry : @{};
}
- (void)addText:(NSString *)text size:(CGFloat)size bold:(BOOL)bold {
    UILabel *label = [[UILabel alloc] init];
    label.numberOfLines = 0;
    label.text = text;
    label.textColor = [UIColor colorWithWhite:0.12 alpha:1];
    label.font = bold ? [UIFont boldSystemFontOfSize:size] : [UIFont systemFontOfSize:size];
    [self.rows addObject:label]; [self.scroll addSubview:label];
}
- (UIButton *)addButton:(NSString *)text action:(SEL)action {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    [button setTitle:text forState:UIControlStateNormal];
    button.titleLabel.numberOfLines = 0;
    button.titleLabel.textAlignment = NSTextAlignmentCenter;
    button.titleLabel.font = [UIFont systemFontOfSize:21];
    button.backgroundColor = [UIColor colorWithWhite:0.95 alpha:1];
    button.layer.cornerRadius = 8;
    button.contentEdgeInsets = UIEdgeInsetsMake(10, 12, 10, 12);
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    [self.rows addObject:button]; [self.scroll addSubview:button];
    return button;
}
- (NSString *)result:(AdaTestResult)result {
    NSArray *ru = @[@"не проверено", @"подтверждено", @"есть ограничения", @"пропущено", @"доступ запрещён", @"ошибка проверки"];
    NSArray *en = @[@"not checked", @"confirmed", @"limited", @"skipped", @"permission denied", @"test failed"];
    return (self.store.state.russian ? ru : en)[result];
}
- (void)render {
    self.title = [self ru:@"Настройка AdaOS" en:@"Set up AdaOS"];
    for (UIView *view in self.rows) [view removeFromSuperview];
    self.rows = [NSMutableArray array];
    AdaOnboarding s = self.store.state;
    [self addText:[NSString stringWithFormat:[self ru:@"Шаг %u из 10" en:@"Step %u of 10"], s.step + 1] size:16 bold:NO];
    NSArray *ru = @[@"Добро пожаловать", @"Проверим звук", @"Проверим экран", @"Сенсор: сверху", @"Сенсор: снизу",
        @"Сенсор: слева", @"Сенсор: справа", @"Проверим микрофон", @"Результаты проверки", @"Подключение к подсети"];
    NSArray *en = @[@"Welcome", @"Check the speaker", @"Check the screen", @"Touch: top", @"Touch: bottom",
        @"Touch: left", @"Touch: right", @"Check the microphone", @"Diagnostic results", @"Connect to your subnet"];
    [self addText:(s.russian ? ru : en)[s.step] size:30 bold:YES];
    NSString *text = [self currentPrompt][@"text"];
    [self addText:text ?: [self ru:@"Продолжите настройку кнопками ниже." en:@"Continue using the controls below."] size:23 bold:NO];
    if (self.notice.length) [self addText:self.notice size:19 bold:NO];
    if (self.busy) {
        [self addButton:[self ru:@"Остановить" en:@"Stop"] action:@selector(cancelRecording)];
    } else if (s.step == AdaWelcome) {
        [self addButton:@"Русский" action:@selector(selectRussian)];
        [self addButton:@"English" action:@selector(selectEnglish)];
        [self addButton:[self ru:@"Начать проверку" en:@"Start checks"] action:@selector(continueStep)];
    } else if (s.step == AdaSpeaker) {
        [self addButton:[self ru:@"Послушать / повторить" en:@"Play / repeat"] action:@selector(playPrompt)];
        UIButton *heard = [self addButton:[self ru:@"Слышно разборчиво" en:@"Heard clearly"] action:@selector(confirm)];
        heard.enabled = s.speaker_played;
        [self addButton:[self ru:@"Плохо слышно или не слышно" en:@"Unclear or not heard"] action:@selector(markUnclear)];
        [self addButton:[self ru:@"Пропустить" en:@"Skip"] action:@selector(skip)];
    } else if (s.step == AdaDisplay) {
        [self addText:[self ru:@"Крупный текст: AdaOS 123" en:@"Large text: AdaOS 123"] size:36 bold:YES];
        [self addButton:[self ru:@"Текст читается" en:@"Text is readable"] action:@selector(confirm)];
        [self addButton:[self ru:@"Трудно прочитать" en:@"Difficult to read"] action:@selector(markUnclear)];
        [self addButton:[self ru:@"Пропустить" en:@"Skip"] action:@selector(skip)];
    } else if (s.step >= AdaTouchTop && s.step <= AdaTouchRight) {
        UIView *zones = [[UIView alloc] init]; zones.tag = 400;
        NSArray *names = s.russian ? @[@"СВЕРХУ", @"СНИЗУ", @"СЛЕВА", @"СПРАВА"] : @[@"TOP", @"BOTTOM", @"LEFT", @"RIGHT"];
        for (NSUInteger i = 0; i < 4; i++) {
            UIButton *zone = [UIButton buttonWithType:UIButtonTypeSystem];
            [zone setTitle:names[i] forState:UIControlStateNormal]; zone.tag = i;
            zone.enabled = i == s.step - AdaTouchTop;
            zone.backgroundColor = zone.enabled ? [UIColor colorWithRed:0.82 green:0.91 blue:1 alpha:1] : [UIColor colorWithWhite:0.95 alpha:1];
            zone.titleLabel.font = [UIFont boldSystemFontOfSize:19];
            [zone addTarget:self action:@selector(zoneTapped:) forControlEvents:UIControlEventTouchUpInside];
            [zones addSubview:zone];
        }
        [self.rows addObject:zones]; [self.scroll addSubview:zones];
        [self addButton:[self ru:@"Не работает / не вижу" en:@"Not working / not visible"] action:@selector(markUnclear)];
        [self addButton:[self ru:@"Пропустить" en:@"Skip"] action:@selector(skip)];
    } else if (s.step == AdaMicrophone) {
        BOOL granted = [[LabAudioProbe permission] isEqual:@"granted"];
        [self addButton:granted ? [self ru:@"Записать 3 секунды и прослушать" en:@"Record 3 seconds and replay"] :
            [self ru:@"Разрешить микрофон" en:@"Allow microphone"] action:granted ? @selector(recordMicrophone) : @selector(requestMicrophone)];
        if (s.microphone_replayed) {
            [self addButton:[self ru:@"Запись разборчива" en:@"Replay is clear"] action:@selector(confirm)];
            [self addButton:[self ru:@"Запись плохо слышно" en:@"Replay is unclear"] action:@selector(markUnclear)];
        }
        [self addButton:[self ru:@"Продолжить без микрофона" en:@"Continue without microphone"] action:@selector(skip)];
    } else if (s.step == AdaSummary) {
        NSString *summary = [NSString stringWithFormat:[self ru:@"Звук: %@\nЭкран: %@\nСенсор: %@ / %@ / %@ / %@\nМикрофон: %@"
            en:@"Speaker: %@\nDisplay: %@\nTouch: %@ / %@ / %@ / %@\nMicrophone: %@"],
            [self result:s.speaker], [self result:s.display], [self result:s.touch[0]], [self result:s.touch[1]],
            [self result:s.touch[2]], [self result:s.touch[3]], [self result:s.microphone]];
        [self addText:summary size:21 bold:NO];
        [self addButton:[self ru:@"Подключить к AdaOS" en:@"Connect to AdaOS"] action:@selector(continueStep)];
        [self addButton:[self ru:@"Повторить диагностику" en:@"Repeat diagnostics"] action:@selector(resetDiagnostics)];
    } else {
        [self addText:[self ru:@"Результаты сохранены. Подключение требует подтверждения владельца подсети."
            en:@"Results saved. Connection requires subnet owner approval."] size:20 bold:NO];
        [self addText:[self ru:@"После подключения настроим голос: проверим микрофон и узел, попробуем обращение «Арсений» и попросим подтвердить слышимость ответа. Длительное прослушивание включается по желанию."
            en:@"After connecting, set up voice: check microphone and node, try Arseniy and confirm that you hear the reply. Long listening is optional."] size:20 bold:NO];
        if (self.connectionFactory) [self addButton:[self ru:@"Подключиться и настроить голос" en:@"Connect and set up voice"] action:@selector(openConnection)];
    }
    if (!self.busy) {
        [self addButton:s.guidance ? [self ru:@"Голосовые подсказки: включены" en:@"Voice guidance: on"] :
            [self ru:@"Голосовые подсказки: выключены" en:@"Voice guidance: off"] action:@selector(toggleGuidance)];
        if (s.step != AdaSpeaker) [self addButton:[self ru:@"Повторить подсказку" en:@"Repeat prompt"] action:@selector(playPrompt)];
    }
    [self.view setNeedsLayout];
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    self.scroll.frame = self.view.bounds;
    CGFloat width = CGRectGetWidth(self.scroll.bounds), left = MAX(16, (width - 620) / 2), w = width - 2 * left;
    CGFloat y = 20;
    for (UIView *row in self.rows) {
        CGFloat height;
        if ([row isKindOfClass:[UILabel class]]) height = ceil([row sizeThatFits:CGSizeMake(w, CGFLOAT_MAX)].height) + 8;
        else if (row.tag == 400) height = 300;
        else height = MAX(56, ceil([[(UIButton *)row titleLabel] sizeThatFits:CGSizeMake(w - 24, CGFLOAT_MAX)].height) + 22);
        row.frame = CGRectMake(left, y, w, height); y += height + 12;
        if (row.tag == 400) for (UIButton *zone in row.subviews) {
            if (zone.tag == 0) zone.frame = CGRectMake(0, 0, w, 64);
            else if (zone.tag == 1) zone.frame = CGRectMake(0, 236, w, 64);
            else zone.frame = CGRectMake(zone.tag == 2 ? 0 : w / 2 + 6, 114, w / 2 - 6, 72);
        }
    }
    self.scroll.contentSize = CGSizeMake(width, y + 20);
}
- (void)stopPrompt {
    self.prompt.delegate = nil; [self.prompt stop]; self.prompt = nil;
    if (!self.audio.running) [[AVAudioSession sharedInstance] setActive:NO
        withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
}
- (void)playPrompt {
    if (self.busy || self.audio.running) return;
    [self stopPrompt];
    NSString *name = [self currentPrompt][@"audio"];
    if (![name isKindOfClass:[NSString class]] || [name containsString:@"/"]) return;
    NSURL *url = [[NSBundle mainBundle] URLForResource:name.stringByDeletingPathExtension withExtension:@"wav" subdirectory:@"Voice"];
    AVAudioSession *session = [AVAudioSession sharedInstance];
    if (!url || ![session setCategory:AVAudioSessionCategoryPlayback error:nil] || ![session setActive:YES error:nil]) return;
    self.prompt = [[AVAudioPlayer alloc] initWithContentsOfURL:url error:nil];
    self.prompt.delegate = self;
    if (self.prompt.duration > 45 || ![self.prompt play]) {
        [self stopPrompt];
        self.notice = [self ru:@"Звук недоступен. Продолжите по тексту." en:@"Audio unavailable. Continue using the text."];
        [self render];
    }
}
- (void)audioPlayerDidFinishPlaying:(AVAudioPlayer *)player successfully:(BOOL)flag {
    if (player != self.prompt) return;
    if (flag && self.store.state.step == AdaSpeaker) [self.store apply:AdaPromptCompleted];
    [self stopPrompt]; [self render];
}
- (void)advance:(AdaOnboardingEvent)event {
    if (self.busy) return;
    [self stopPrompt]; self.notice = nil;
    if ([self.store apply:event]) { [self render]; [self.scroll setContentOffset:CGPointZero animated:NO]; if (self.store.state.guidance) [self playPrompt]; }
}
- (void)continueStep { [self advance:AdaContinue]; }
- (void)confirm { [self advance:AdaConfirm]; }
- (void)markUnclear { [self advance:AdaMarkUnclear]; }
- (void)skip { [self advance:AdaSkip]; }
- (void)zoneTapped:(UIButton *)button {
    if (self.store.state.step >= AdaTouchTop && self.store.state.step <= AdaTouchRight &&
        button.tag == (NSInteger)(self.store.state.step - AdaTouchTop)) [self confirm];
}
- (void)selectLanguage:(BOOL)russian {
    [self stopPrompt]; AdaOnboarding state = self.store.state; state.russian = russian;
    self.store.state = state; [self.store save]; [self render]; if (state.guidance) [self playPrompt];
}
- (void)selectRussian { [self selectLanguage:YES]; }
- (void)selectEnglish { [self selectLanguage:NO]; }
- (void)toggleGuidance {
    [self stopPrompt]; AdaOnboarding state = self.store.state; state.guidance = !state.guidance;
    self.store.state = state; [self.store save]; [self render]; if (state.guidance) [self playPrompt];
}
- (void)requestMicrophone {
    [self stopPrompt];
    [LabAudioProbe requestPermissionWithCompletion:^(NSString *status) {
        (void)status;
        if (self.store.state.step != AdaMicrophone) return;
        if ([[LabAudioProbe permission] isEqual:@"denied"]) [self advance:AdaPermissionDenied];
        else { self.notice = [self ru:@"Доступ разрешён. Нажмите «Записать», когда будете готовы." en:@"Permission granted. Press Record when ready."]; [self render]; }
    }];
}
- (void)recordMicrophone {
    if (self.busy || self.store.state.step != AdaMicrophone) return;
    [self stopPrompt]; self.busy = YES; NSUInteger generation = ++self.generation;
    [self.store apply:AdaReplayStarted];
    self.notice = [self ru:@"Говорите сейчас. Запись: 3 секунды, затем прослушивание." en:@"Speak now. Recording for 3 seconds, then replaying."];
    [self render];
    [self.audio recordShortSampleAndReplayWithCompletion:^(NSString *status) {
        if (generation != self.generation) return;
        self.busy = NO;
        BOOL ok = [status hasPrefix:@"Audio replay OK."] && [status hasSuffix:@"Local test file removed."];
        [self.store apply:ok ? AdaReplaySucceeded : AdaReplayFailed];
        self.notice = ok ? [self ru:@"Слышна ли ваша фраза разборчиво? Запись удалена." en:@"Was your phrase clear? Recording deleted."] :
            [self ru:@"Проверка не завершилась. Повторите или продолжите без микрофона." en:@"Test did not complete. Retry or continue without microphone."];
        [self render];
    }];
}
- (void)cancelRecording {
    ++self.generation; [self.audio cancel]; self.busy = NO;
    self.notice = [self ru:@"Проверка остановлена. Можно повторить." en:@"Test stopped. You can try again."];
    [self render];
}
- (void)resetDiagnostics {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:[self ru:@"Повторить проверки?" en:@"Repeat checks?"]
        message:[self ru:@"Результаты диагностики будут заменены. Подключение к AdaOS сохранится." en:@"Diagnostic results will be replaced. Your AdaOS connection is retained."] preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Отмена" en:@"Cancel"] style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Повторить" en:@"Repeat"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        (void)action; [self stopPrompt]; AdaOnboarding s; AdaOnboardingInit(&s);
        s.russian = self.store.state.russian; s.guidance = self.store.state.guidance;
        self.store.state = s; [self.store save]; self.notice = nil; [self render];
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}
- (void)showMore {
    if (self.busy) return;
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"AdaOS" message:nil preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Лабораторные проверки" en:@"Lab checks"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        (void)action; if (self.labFactory) [self.navigationController pushViewController:self.labFactory() animated:YES];
    }]];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Повторить диагностику" en:@"Repeat diagnostics"] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        (void)action; [self resetDiagnostics];
    }]];
    [alert addAction:[UIAlertAction actionWithTitle:[self ru:@"Отмена" en:@"Cancel"] style:UIAlertActionStyleCancel handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}
- (void)openConnection {
    if (self.connectionFactory) [self.navigationController pushViewController:self.connectionFactory() animated:YES];
}
- (void)interrupted:(NSNotification *)notification {
    (void)notification; [self stopPrompt];
    if (self.busy) [self cancelRecording];
}
- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated]; [self stopPrompt]; if (self.busy) [self cancelRecording];
}
- (void)dealloc { [self.audio cancel]; [[NSNotificationCenter defaultCenter] removeObserver:self]; }
@end
