<?php

namespace App\Support;

use InvalidArgumentException;

final class InputError extends InvalidArgumentException
{
    public function __construct(public array $detail)
    {
        parent::__construct('validation failed');
    }
}
