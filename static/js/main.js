document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');

    if (searchBtn) {
        searchBtn.addEventListener('click', handleSearch);
    }
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') handleSearch();
        });
    }
});

function handleSearch() {
    const input = document.getElementById('searchInput');
    const query = input.value.trim().toUpperCase();
    if (query) {
        window.location.href = `/stock/${encodeURIComponent(query)}`;
    }
}
