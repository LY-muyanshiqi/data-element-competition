"""验证引擎单元测试"""
import pytest
from verify_engine import (
    ReferenceRecord, VerificationResult, verify_record, verify_batch_concurrent,
    SemanticChecker, _check_doi_format, _check_title_ai_fingerprints,
    _check_author_match, _check_journal_match, clear_cache,
)


class TestSemanticChecker:
    def test_is_chinese_title(self):
        assert SemanticChecker.is_chinese_title('基于深度学习的洪水预测模型研究')
        assert not SemanticChecker.is_chinese_title('Deep Learning for Flood Prediction')

    def test_classify_language(self):
        assert SemanticChecker.classify_language('洪水预测模型研究') == 'zh'
        assert SemanticChecker.classify_language('Deep Learning') == 'en'
        assert SemanticChecker.classify_language('基于CNN的Flood预测') == 'mixed'

    def test_check_title_length_zh(self):
        score, _ = SemanticChecker.check_title_length('深度学习洪水预测模型研究')
        assert score == 3
        score, _ = SemanticChecker.check_title_length('短')
        assert score == 0

    def test_check_garbage_chars(self):
        score, _ = SemanticChecker.check_garbage_chars('正常标题')
        assert score == 3
        score, _ = SemanticChecker.check_garbage_chars('\x00\x01\x02')
        assert score == 0

    def test_check_year_range_zh(self):
        score, _ = SemanticChecker.check_year_range(2023, 'zh')
        assert score == 4
        score, _ = SemanticChecker.check_year_range(1970, 'zh')
        assert score == 0


class TestDOICheck:
    def test_valid_doi(self):
        s, _ = _check_doi_format('10.1038/nature12373')
        assert s == 8

    def test_invalid_doi(self):
        s, _ = _check_doi_format('not-a-doi')
        assert s == 0

    def test_missing_doi(self):
        s, _ = _check_doi_format(None)
        assert s == 0


class TestTitleAIFingerprints:
    def test_clean_title(self):
        s, _ = _check_title_ai_fingerprints('Nanometre-scale thermometry in a living cell')
        assert s == 7

    def test_exaggerated_title(self):
        s, _ = _check_title_ai_fingerprints(
            'A Novel Approach for Predicting Stock Market Crashes with 100% Accuracy in Linear Time'
        )
        assert s <= 3


class TestVerifyRecord:
    def test_real_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='Nanometre-scale thermometry in a living cell',
            authors='G. Kucsko, P. C. Maurer',
            journal='Nature',
            doi='10.1038/nature12373',
            year=2013,
        )
        r = verify_record(ref)
        assert r.status == '可靠'
        assert r.score >= 80

    def test_fake_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='A Novel Quantum Computing Approach for Solving NP-Complete Problems in Linear Time',
            authors='Fictitious Author',
            doi='10.9999/fake.paper.2024',
            year=2024,
        )
        r = verify_record(ref)
        assert r.score < 40

    def test_no_doi_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='Attention Is All You Need',
            authors='Ashish Vaswani',
            year=2017,
        )
        r = verify_record(ref)
        assert r.score >= 0


class TestBatchConcurrent:
    def test_batch_three(self):
        clear_cache()
        refs = [
            ReferenceRecord(title='Deep Residual Learning for Image Recognition', authors='Kaiming He', year=2016),
            ReferenceRecord(title='Fake Paper Title That Does Not Exist', authors='Fake', year=2025),
            ReferenceRecord(title='BERT: Pre-training of Deep Bidirectional Transformers', authors='Jacob Devlin', year=2019),
        ]
        results = verify_batch_concurrent(refs, max_workers=3)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, VerificationResult)
