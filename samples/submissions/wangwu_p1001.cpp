#include <iostream>
using namespace std;

int main() {
    volatile long long x = 0;
    while (true) {
        x = x + 1;
    }
    return 0;
}
