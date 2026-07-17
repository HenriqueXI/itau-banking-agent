"""Masking processor — one test per PII pattern (PRD-001 acceptance table)."""

from shared.logging.masking import mask_mapping, mask_pii, mask_value
from shared.logging.processors import mask_pii_processor


class TestCpfMasking:
    def test_formatted_cpf(self) -> None:
        assert mask_pii("CPF do cliente: 123.456.789-09") == "CPF do cliente: ***.456.789-**"

    def test_bare_11_digit_number_is_masked(self) -> None:
        # Could be CPF or phone — either way it must not survive unmasked.
        masked = mask_pii("documento 12345678909")
        assert "12345678909" not in masked

    def test_cpf_inside_sentence(self) -> None:
        masked = mask_pii("transferir para 987.654.321-00 hoje")
        assert "987.654.321-00" not in masked
        assert "***.654.321-**" in masked


class TestEmailMasking:
    def test_email_masked_keeping_prefix(self) -> None:
        assert mask_pii("login de ana@demo.example.com") == "login de ana****"

    def test_email_with_dots_and_plus(self) -> None:
        masked = mask_pii("contato: joao.silva+bank@example.com.br")
        assert "@" not in masked
        assert masked.startswith("contato: joa")


class TestPhoneMasking:
    def test_mobile_with_ddd(self) -> None:
        masked = mask_pii("ligar para (11) 91234-5678")
        assert "5678" in masked
        assert "91234" not in masked

    def test_international_format(self) -> None:
        masked = mask_pii("tel +55 11 91234-5678")
        assert "91234-5678" not in masked
        assert masked.endswith("5678")


class TestPixKeyMasking:
    def test_random_evp_key_masked(self) -> None:
        key = "9d13a7f2-4b8e-4f6a-9c3d-2e1f0a9b8c7d"
        masked = mask_pii(f"chave pix {key}")
        assert key not in masked
        assert "9d13****" in masked


class TestMaskValue:
    def test_nested_structures(self) -> None:
        masked = mask_value(
            {
                "user": {"email": "ana@demo.example", "cpf": "123.456.789-09"},
                "notes": ["pix para 111.222.333-44"],
                "amount": 1500,
            }
        )
        assert masked["user"]["email"] == "ana****"
        assert masked["user"]["cpf"] == "***.456.789-**"
        assert "111.222.333-44" not in masked["notes"][0]
        assert masked["amount"] == 1500

    def test_operation_hash_is_preserved_for_audit_correlation(self) -> None:
        operation_hash = "9d13a7f2-4b8e-4f6a-9c3d-2e1f0a9b8c7d"
        assert mask_mapping({"operation_hash": operation_hash}) == {
            "operation_hash": operation_hash
        }


class TestProcessor:
    def test_masks_event_dict_values(self) -> None:
        event_dict = {"event": "login", "email": "carla@demo.example"}
        result = mask_pii_processor(None, "info", event_dict)
        assert result["email"] == "car****"

    def test_correlation_ids_never_masked(self) -> None:
        trace_id = "3f2b8c9d-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
        result = mask_pii_processor(None, "info", {"trace_id": trace_id, "event": "x"})
        assert result["trace_id"] == trace_id
