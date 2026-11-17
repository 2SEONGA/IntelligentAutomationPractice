
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="공공 SI 입찰 PDF 자동 검토")

    # 입력 설정
    parser.add_argument("--input-dir", default=str(PROJECT_ROOT / "data/input"))
    parser.add_argument("--rfp")
    parser.add_argument("--notice-pdf")
    parser.add_argument("--company", default=str(PROJECT_ROOT / "data/company/company"), help="기본값: 제공된 회사 공개자료 JSON")
    parser.add_argument("--market")
    parser.add_argument("--notice")

    # 실행 설정
    parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs/all_pdfs"))
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config/settings.yaml"))
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--crew-verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--save-intermediate", action="store_true")
    parser.add_argument("--allow-market-api", action="store_true", help="live 모드에서 검증된 공고문이 있을 때 나라장터 API 허용")

    args = parser.parse_args()
    if args.allow_market_api and args.mode != "live":
        parser.error("--allow-market-api는 --mode live에서만 사용할 수 있습니다.")
    from dotenv import load_dotenv
    import os
    load_dotenv(PROJECT_ROOT / ".env")
    if args.mode == "live" and not os.getenv("OPENAI_API_KEY"):
        parser.error(".env에 본인 OPENAI_API_KEY를 입력하세요. 키 없이 확인하려면 --mode offline을 사용하세요.")

    # 1. PDF 자동 탐색
    batch = args.rfp is None

    if batch:
        input_dir = Path(args.input_dir)

        if not input_dir.is_dir():
            parser.error(f"입력 폴더가 없습니다: {input_dir}")

        if args.notice_pdf:
            parser.error("--notice-pdf는 --rfp와 함께 사용하세요.")

        pdf_files = sorted(
            (
                p for p in input_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".pdf"
            ),
            key=lambda p: p.name.casefold()
        )
    else:
        pdf_files = [Path(args.rfp)]

    if not pdf_files:
        parser.error("입력 폴더에 PDF 파일이 없습니다.")

    if args.company and not Path(args.company).is_file():
        parser.error(f"회사 JSON 파일이 없습니다: {args.company}")

    if not Path(args.config).is_file():
        parser.error(f"설정 파일이 없습니다: {args.config}")

    try:
        notice = json.loads(Path(args.notice).read_text(encoding="utf-8")) if args.notice else None
    except (OSError, ValueError) as exc:
        parser.error(f"공고 메타데이터 JSON을 읽을 수 없습니다: {exc}")
    from .crew import BidReviewCrew
    print("실행 방식: " + ("규칙 기반 검사 (LLM·외부 API 호출 없음)" if args.mode == "offline" else "실제 CrewAI Task 실행 (2·3 병렬)"))

    # 2. PDF를 하나씩 순차적으로 실행
    failed = 0

    for index, pdf_path in enumerate(pdf_files, start=1):

        if not pdf_path.is_file():
            print(f"[실패] PDF 없음: {pdf_path}")
            failed += 1
            continue

        # PDF별 결과 저장 폴더
        output_dir = (
            Path(args.output) / f"{index:03d}_{pdf_path.stem}"
            if batch else Path(args.output)
        )

        print(f"\n[{index}/{len(pdf_files)}] 분석 시작: {pdf_path.name}")

        try:
            crew = BidReviewCrew(
                args.config,
                verbose=args.verbose,
                trace=args.trace,
                crew_verbose=args.crew_verbose
            )

            result = crew.run(
                pdf_path=pdf_path,
                company_path=args.company,
                output_dir=output_dir,
                market_path=args.market,
                notice=notice,
                mode=args.mode,
                notice_pdf=args.notice_pdf,
                save_intermediate=args.debug or args.save_intermediate,
                allow_market_api=args.allow_market_api,
            )

            print(f"보고서: {output_dir / result['report_artifacts'][0]}")
            print(f"실행 상태: {result['manifest'].get('mode', '미확인')}")

            skipped = result["manifest"].get("skipped", [])

            if skipped:
                print("생략된 단계: " + ", ".join(skipped))

        except Exception as e:
            failed += 1
            print(f"[실패] {pdf_path.name}: {type(e).__name__}: {e}")
            continue

    print(
        f"\n전체 {len(pdf_files)}개 중 "
        f"{len(pdf_files) - failed}개 처리, {failed}개 오류"
    )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
