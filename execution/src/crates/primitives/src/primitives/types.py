from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class U256:
    """Unsigned 256-bit integer with deterministic fixed-width semantics."""

    BITS: ClassVar[int] = 256
    BYTE_LENGTH: ClassVar[int] = 32
    MODULUS: ClassVar[int] = 1 << BITS
    MAX_VALUE: ClassVar[int] = MODULUS - 1

    _value: int

    def __post_init__(self) -> None:
        if not isinstance(self._value, int):
            raise TypeError("U256 value must be an integer")
        if not 0 <= self._value <= self.MAX_VALUE:
            raise ValueError("U256 value must fit in 256 bits")

    @classmethod
    def zero(cls) -> "U256":
        return cls(0)

    @classmethod
    def one(cls) -> "U256":
        return cls(1)

    @classmethod
    def max_value(cls) -> "U256":
        return cls(cls.MAX_VALUE)

    @classmethod
    def from_hex(cls, value: str) -> "U256":
        if not isinstance(value, str):
            raise TypeError("hex value must be a string")
        normalized = value[2:] if value.startswith(("0x", "0X")) else value
        if not normalized:
            raise ValueError("hex value cannot be empty")
        return cls(int(normalized, 16))

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview, byteorder: str = "big") -> "U256":
        raw = bytes(data)
        if len(raw) > cls.BYTE_LENGTH:
            raise ValueError("U256 byte representation cannot exceed 32 bytes")
        if byteorder not in {"big", "little"}:
            raise ValueError("byteorder must be 'big' or 'little'")
        return cls(int.from_bytes(raw, byteorder=byteorder, signed=False))

    @staticmethod
    def _coerce_other(other: object) -> int:
        if isinstance(other, U256):
            return other._value
        if isinstance(other, int):
            return other
        return NotImplemented

    @classmethod
    def _wrap(cls, value: int) -> "U256":
        return cls(value & cls.MAX_VALUE)

    def checked_add(self, other: int | "U256") -> "U256":
        result, overflowed = self.overflowing_add(other)
        if overflowed:
            raise OverflowError("U256 addition overflow")
        return result

    def overflowing_add(self, other: int | "U256") -> tuple["U256", bool]:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            raise TypeError("unsupported operand type for U256 addition")
        result = self._value + other_value
        return self._wrap(result), result > self.MAX_VALUE

    def checked_sub(self, other: int | "U256") -> "U256":
        result, overflowed = self.overflowing_sub(other)
        if overflowed:
            raise OverflowError("U256 subtraction underflow")
        return result

    def overflowing_sub(self, other: int | "U256") -> tuple["U256", bool]:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            raise TypeError("unsupported operand type for U256 subtraction")
        result = self._value - other_value
        return self._wrap(result), result < 0

    def checked_mul(self, other: int | "U256") -> "U256":
        result, overflowed = self.overflowing_mul(other)
        if overflowed:
            raise OverflowError("U256 multiplication overflow")
        return result

    def overflowing_mul(self, other: int | "U256") -> tuple["U256", bool]:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            raise TypeError("unsupported operand type for U256 multiplication")
        result = self._value * other_value
        return self._wrap(result), result > self.MAX_VALUE

    def to_bytes(self, length: int = BYTE_LENGTH, byteorder: str = "big") -> bytes:
        if length < 0:
            raise ValueError("length must be non-negative")
        if byteorder not in {"big", "little"}:
            raise ValueError("byteorder must be 'big' or 'little'")
        if self._value.bit_length() > length * 8:
            raise OverflowError("value does not fit in requested byte length")
        return self._value.to_bytes(length, byteorder=byteorder, signed=False)

    def to_hex(self, prefix: bool = True) -> str:
        encoded = f"{self._value:064x}"
        return f"0x{encoded}" if prefix else encoded

    def bit_length(self) -> int:
        return self._value.bit_length()

    def is_zero(self) -> bool:
        return self._value == 0

    def __int__(self) -> int:
        return self._value

    def __index__(self) -> int:
        return self._value

    def __bool__(self) -> bool:
        return self._value != 0

    def __repr__(self) -> str:
        return f"U256({self.to_hex()})"

    def __str__(self) -> str:
        return self.to_hex()

    def __lt__(self, other: object) -> bool:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._value < other_value

    def __le__(self, other: object) -> bool:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._value <= other_value

    def __gt__(self, other: object) -> bool:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._value > other_value

    def __ge__(self, other: object) -> bool:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._value >= other_value

    def __add__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._wrap(self._value + other_value)

    def __radd__(self, other: object) -> "U256":
        return self.__add__(other)

    def __sub__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._wrap(self._value - other_value)

    def __rsub__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._wrap(other_value - self._value)

    def __mul__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return self._wrap(self._value * other_value)

    def __rmul__(self, other: object) -> "U256":
        return self.__mul__(other)

    def __floordiv__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        if other_value == 0:
            raise ZeroDivisionError("division by zero")
        return U256(self._value // other_value)

    def __truediv__(self, other: object) -> "U256":
        return self.__floordiv__(other)

    def __mod__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        if other_value == 0:
            raise ZeroDivisionError("modulo by zero")
        return U256(self._value % other_value)

    def __divmod__(self, other: object) -> tuple["U256", "U256"]:
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        if other_value == 0:
            raise ZeroDivisionError("division by zero")
        quotient, remainder = divmod(self._value, other_value)
        return U256(quotient), U256(remainder)

    def __and__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return U256(self._value & other_value)

    def __rand__(self, other: object) -> "U256":
        return self.__and__(other)

    def __or__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return U256(self._value | other_value)

    def __ror__(self, other: object) -> "U256":
        return self.__or__(other)

    def __xor__(self, other: object) -> "U256":
        other_value = self._coerce_other(other)
        if other_value is NotImplemented:
            return NotImplemented
        return U256(self._value ^ other_value)

    def __rxor__(self, other: object) -> "U256":
        return self.__xor__(other)

    def __invert__(self) -> "U256":
        return U256(self.MAX_VALUE ^ self._value)

    def __lshift__(self, other: object) -> "U256":
        if not isinstance(other, int):
            return NotImplemented
        if other < 0:
            raise ValueError("shift count must be non-negative")
        return self._wrap(self._value << other)

    def __rshift__(self, other: object) -> "U256":
        if not isinstance(other, int):
            return NotImplemented
        if other < 0:
            raise ValueError("shift count must be non-negative")
        return U256(self._value >> other)


@dataclass(frozen=True, slots=True)
class _FixedBytes:
    SIZE: ClassVar[int] = 0

    _bytes: bytes

    def __post_init__(self) -> None:
        raw = bytes(self._bytes)
        if len(raw) != self.SIZE:
            raise ValueError(f"{self.__class__.__name__} must be exactly {self.SIZE} bytes")
        object.__setattr__(self, "_bytes", raw)

    @classmethod
    def from_hex(cls, value: str) -> "_FixedBytes":
        if not isinstance(value, str):
            raise TypeError("hex value must be a string")
        normalized = value[2:] if value.startswith(("0x", "0X")) else value
        if len(normalized) != cls.SIZE * 2:
            raise ValueError(f"{cls.__name__} hex value must be exactly {cls.SIZE * 2} characters")
        try:
            return cls(bytes.fromhex(normalized))
        except ValueError as exc:
            raise ValueError(f"invalid hex for {cls.__name__}") from exc

    @classmethod
    def zero(cls) -> "_FixedBytes":
        return cls(bytes(cls.SIZE))

    def to_bytes(self) -> bytes:
        return self._bytes

    def to_hex(self, prefix: bool = True) -> str:
        encoded = self._bytes.hex()
        return f"0x{encoded}" if prefix else encoded

    def __bytes__(self) -> bytes:
        return self._bytes

    def __len__(self) -> int:
        return self.SIZE

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_hex()})"

    def __str__(self) -> str:
        return self.to_hex()


@dataclass(frozen=True, slots=True)
class Address(_FixedBytes):
    SIZE: ClassVar[int] = 20


@dataclass(frozen=True, slots=True)
class Hash(_FixedBytes):
    SIZE: ClassVar[int] = 32
