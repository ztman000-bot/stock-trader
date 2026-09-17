# v0.17.17 운영 증거 보호

Control v0.8.0 LOCKED / APP_MODE=paper / ENABLE_TRADING=False / REAL ORDER OFF /
068270 영구 제외를 유지한다. 이 변경은 운영 상태와 연구 자료 보존에만 적용된다.
Control/Paper의 진입·청산·Risk, 수집 시간, 공식 GOOD 기준, Exit Replay 기준은 변경하지 않는다.
수익성 또는 실제 주문 lifecycle 검증을 완료했다는 의미가 아니다.

## 1. 검증된 DB 백업 세트

`db_backup.py`는 SQLite backup API로 존재하는 다음 DB를 각각 복사한다.

| DB | 경로 설정 |
| --- | --- |
| market_data.db | MARKET_DB_PATH |
| research_experiments.db | server/ 고정 경로 |
| regime_reference.db | REGIME_REFERENCE_DB_PATH |
| historical_market.db | HISTORICAL_MARKET_DB_PATH |
| us_market_data.db | US_MARKET_DB_PATH |
| execution_simulation.db | server/ 고정 경로 |

market_data.db는 필수다. 아직 생성되지 않은 선택 DB는 manifest의 `notCreated`에 기록한다.
한 번 백업에 포함된 DB가 사라지면 다음 백업은 실패한다. 새 DB가 생성되면 기존 백업의
`coverageComplete`가 false가 되어 다음 일일 백업 대상이 된다. `.env`, 키, 로그는 포함하지 않는다.

각 파일의 크기·SHA-256·quick_check를 기록하고 별도 임시 디렉터리로 다시 복사해 검증한다.
모든 검증을 마친 세트만 공개한 뒤 보존 개수만큼 회전한다. 기존 단일 DB 백업은 이행 중 보존한다.
manifest에는 DB별 복사 시각을 남긴다. **여러 DB 전체가 한 시점에 원자적으로 복사되는 것은 아니다.**
DB 간 거래 일관성을 보장하는 재해복구 인증 또는 실서버 복구 완료 판정으로 사용하지 않는다.
복원 검사는 SQLite 읽기·해시 검증이며, 앱을 별도 서버로 실행하는 검사는 아니다.

백업 저장 공간에는 전체 세트 외에 복원 검사용 가장 큰 DB 한 개의 여유가 필요하다.
동시 백업은 거절하며, 디스크 부족·복원 검사 실패 시 이전 검증 세트를 보존한다.

```bash
cd ~/stock-trader
python server/db_backup.py --status
python server/db_backup.py --once --reason manual-verified-set
python server/db_backup.py --verify server/backups/backup-set-실제이름
```

`--status`는 파일 목록·크기를 가볍게 확인한다. 표시되는 `restoreVerified`는 백업 생성 당시
복원 검사의 결과다. 현재 시점 전체 해시/SQLite 재검사는 `--verify`를 사용한다.

복원은 **새 staging 디렉터리만** 허용한다. 실행 중인 DB를 교체하지 않는다.

```bash
python server/db_backup.py --restore-from server/backups/backup-set-실제이름 --restore-to ~/stock-trader-restore-review
```

암호화 offsite 백업은 기존 명시적 opt-in 설정을 유지하며, 이미 켜진 경우 전체 세트 tar를
기존 OpenSSL 방식으로 암호화하여 기존 HTTPS 목적지로 보낸다. 상태에 DB 목록과 복원 검사
결과를 함께 남긴다. 구버전 암호문은 단일 SQLite이고 새 버전은 tar이므로, 복호화한 새 tar는
`--restore-from /안전한/경로/bundle.tar --restore-to /새/검토폴더`로 검사한다.
이 업데이트가 외부 목적지를 설정하거나 offsite 업로드를 새로 켜지는 않는다.

## 2. Android 앱 업데이트 완료 검증

네이티브 앱의 서버 업데이트 버튼은 두 번 누르기 확인을 유지한다. 서버는 기존 로컬 변경,
열린 Paper 포지션, paper 모드, Tailscale/localhost 보호를 유지한다.

요청마다 ID를 만들고 `~/.stock-trader-update-receipt.json`에 최신 요청 결과를 원자적으로 저장한다.
분리 실행한 감독 프로세스는 기존 `android_update.sh`와 자신의 private 복사본을 실행한다.
서버가 종료되어도 요청 기록과 감독 프로세스는 남는다. 기록은 최신 1건이며 전체 감사 이력은 아니다.

완료 표시에는 다음 증거가 모두 필요하다.

- 해당 요청 ID와 목표 main SHA 일치
- 기존 안전 updater의 테스트·백업·재시작 단계 성공
- 디스크 HEAD와 실행 서버가 부팅할 때 기록한 SHA 일치, 새 PID, paper 안전 상태
- market reference sync 성공 또는 304, 시장별 schema/date가 checkout의 aggregate 메타데이터 충족

참고자료가 설정에서 명시적으로 꺼져 있으면 그 상태를 표시한다. 코드 확인 후 참고자료 검사만
실패하면 `COMPLETED_WITH_WARNINGS`로 표시하며, 완료 초록색으로 표시하지 않는다. 실제 주문은
계속 OFF다. 롤백·오류·감독 프로세스 중단은 실패/중단으로 표시하고 PID 변경만으로 성공하지 않는다.
다운로드 중 main이 더 진행해 목표 SHA와 달라져도 성공을 추정하지 않는다.

화면을 닫았다 열면 저장된 요청 확인을 이어간다. 10분 안에 끝나지 않아도 자동 재요청하지 않는다.
검증 중에는 추가 업데이트가 차단된다. liveness는 DB 조회 없이 부팅 SHA만 추가로 반환한다.

**이행:** 이 버전을 처음 설치하는 업데이트는 이전 버전의 실행기/화면이 시작한 작업이다.
새 영속 검증 프로토콜은 이 버전 설치 후 시작하는 업데이트부터 적용된다. 최초 설치 직후
`NO_RECEIPT`는 실패가 아니라 이전 작업의 검증 기록이 없다는 뜻이다. 아래 상태의
`operationsVersion=0.17.17`과 `runningCommit`을 main SHA와 비교해 설치 여부를 확인한다.

```bash
curl -fsS http://127.0.0.1:8000/api/system/update/status
```

APK 재빌드는 필요 없다. 서버 제공 UI 자산과 Data Health 캐시 버전도 갱신한다.
Windows의 별도 updater와 구형 일반 웹 대시보드의 업데이트 UI는 이 프로토콜 대상이 아니다.

## 3. 독립 거래일 기준 수집 누락 감지

기존 스냅샷 coverage는 DB에 관측된 거래일로 기대 날짜를 정해, 하루 전체가 빠지면 탐지할 수 없었다.
이제 `server/data/krx_sessions.json`의 최근 5개 예정 거래일을 사용한다. 양쪽 bar 테이블에
하루가 없어도 기대 날짜가 남고, 스냅샷 0건은 `wholeMissingDays`에 표시된다.

거래 시간 안에서 완료된 5분 구간만 분모로 사용한다. 같은 구간의 중복 시각은 한 번만 세고,
장 밖·미완료 구간·잘못된 시각은 coverage를 높이지 않는다. 첫 거래일 지연 개장도 반영한다.
기존 forward baseline은 그대로 보존하고 과거 스냅샷을 합성하거나 backfill하지 않는다.
NH 종목별 bar completeness 또는 Paper 수집/매매 스케줄을 변경하지 않는다.

달력은 공식 실시간 KRX 피드가 아닌 운영 참고표다. `exchange-calendars==4.13.2`의 XKRX
일정 사실만 생성했으며 upstream 코드는 복제하지 않았다. 생성 환경에만 이 패키지가 필요하고
공기계 런타임에는 추가 의존성이 없다. 생성 범위는 `2026-01-01`~`2026-10-31`, 시간은 Asia/Seoul이다.
6월 3일 지방선거와 7월 17일 제헌절 휴일을 명시적으로 보완했다.

- [exchange-calendars 원본](https://github.com/gerrymanoim/exchange_calendars)
- [행정안전부: 2026년 지방선거 6월 3일 일정](https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000008&nttId=126047)
- [인사혁신처: 2026년 노동절·제헌절 공휴일 지정](https://www.mpm.go.kr/mpm/comm/newsPress/newsPressRelease/?boardId=bbs_0000000000000029&category=&cntId=4250&mode=view&pageIdx=)

11월 특별 개장시간은 미검토 상태이므로 **2026-11-01 전에 달력을 재검토·갱신해야 한다.**
기한 만료·파일 오류·불충분한 달력 범위에서는 `CALENDAR_UNKNOWN`과
`dataFoundationReady=false`를 반환한다. 데이터가 없다는 이유로 휴장일을 추정하지 않는다.
정부/거래소의 추가 임시휴장 공지가 나오면 이 참고표도 PR/Safety 절차로 갱신한다.

## 검증

새 동작 테스트는 임시 DB/파일, 가짜 네트워크 응답만 사용한다. WAL 미체크포인트 데이터,
실험 DB 불변 트리거, 손상/부분 백업, 위험한 tar, 복원 실패, 동시 백업, 보존 회전,
offsite 평문 정리, 영속 업데이트/롤백/잘못된 SHA, 시장별 schema/date, 전체 거래일 누락,
달력 만료, 네트워크 재연결 후 UI 판정을 확인한다. Node UI 검사는 CI에서 실행하며,
Node가 없는 Android의 로컬 Python Safety에서는 해당 검사만 skip한다.
